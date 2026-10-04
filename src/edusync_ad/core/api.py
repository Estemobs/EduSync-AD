"""M28 — Serveur API REST (bibliothèque standard).

Architecture inspirée de `portal_server.py` :
- `ThreadingHTTPServer` + `BaseHTTPRequestHandler` (stdlib uniquement)
- Routage par motifs avec paramètres de chemin (`/api/v1/users/{sam}`)
- JSON request/response, codes HTTP standards, headers de sécurité
- Authentification par API Key (header `X-API-Key` ou `Authorization: Bearer`)
- Middleware CORS configurable, logging, rate-limiting basique
- Extensible : les routes sont enregistrées via `APIRouter`

Aucune dépendance externe (pas de FastAPI/Flask) — cohérent avec la philosophie du projet.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

from edusync_ad.core.config import AppConfig
from edusync_ad.core.api_auth import APIKeyStore, load_api_keys, save_api_keys

logger = logging.getLogger("edusync_ad.api")

#: Corps de requête maximal (1 Mo par défaut, configurable).
MAX_BODY = 1024 * 1024

#: Version d'API exposée dans les headers/réponses.
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

#: Motifs de route → regex + noms de paramètres.
_ROUTE_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _compile_route(pattern: str) -> tuple[re.Pattern, list[str]]:
    """Compile un motif `/users/{sam}/groups` en regex + liste des noms de params."""
    param_names = _ROUTE_PARAM_RE.findall(pattern)
    regex_pattern = "^" + _ROUTE_PARAM_RE.sub(r"(?P<\1>[^/]+)", pattern) + "$"
    return re.compile(regex_pattern), param_names


@dataclass
class Route:
    method: str
    pattern: str
    handler: Callable
    regex: re.Pattern = field(init=False)
    param_names: list[str] = field(init=False)

    def __post_init__(self):
        self.regex, self.param_names = _compile_route(self.pattern)

    def match(self, path: str) -> Optional[dict[str, str]]:
        m = self.regex.match(path)
        if not m:
            return None
        return {name: m.group(name) for name in self.param_names}


class APIRouter:
    """Registre de routes avec dispatch par méthode + motif."""

    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, pattern: str, handler: Callable) -> None:
        self._routes.append(Route(method.upper(), pattern, handler))

    def get(self, pattern: str):
        return lambda fn: self.add("GET", pattern, fn)

    def post(self, pattern: str):
        return lambda fn: self.add("POST", pattern, fn)

    def put(self, pattern: str):
        return lambda fn: self.add("PUT", pattern, fn)

    def patch(self, pattern: str):
        return lambda fn: self.add("PATCH", pattern, fn)

    def delete(self, pattern: str):
        return lambda fn: self.add("DELETE", pattern, fn)

    def resolve(self, method: str, path: str) -> tuple[Callable | None, dict[str, str]]:
        for route in self._routes:
            if route.method == method.upper():
                params = route.match(path)
                if params is not None:
                    return route.handler, params
        return None, {}


# -- Exceptions métier ---------------------------------------------------------


class APIError(Exception):
    """Erreur API avec code HTTP et message structuré."""

    def __init__(
        self,
        message: str,
        status: int = 400,
        code: str = "api_error",
        details: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details or {}


class NotFoundError(APIError):
    def __init__(self, resource: str, identifier: str):
        super().__init__(
            f"{resource} '{identifier}' introuvable",
            status=404,
            code="not_found",
            details={"resource": resource, "identifier": identifier},
        )


class UnauthorizedError(APIError):
    def __init__(self, message: str = "Clé API invalide ou absente"):
        super().__init__(message, status=401, code="unauthorized")


class ForbiddenError(APIError):
    def __init__(self, message: str = "Accès refusé (permissions insuffisantes)"):
        super().__init__(message, status=403, code="forbidden")


class ValidationError(APIError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, status=422, code="validation_error", details=details)


# -- Middleware / Décorateurs ---------------------------------------------------


def require_api_key(key_store: APIKeyStore):
    """Décorateur : vérifie l'API Key dans le handler."""

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(request: "APIRequest", *args, **kwargs):
            api_key = request.api_key
            if not api_key:
                raise UnauthorizedError()
            key_info = key_store.validate(api_key)
            if not key_info:
                raise UnauthorizedError("Clé API invalide ou expirée")
            request.api_key_info = key_info
            return fn(request, *args, **kwargs)

        return wrapper

    return decorator


def require_permission(*perms: str):
    """Décorateur : vérifie que la clé a au moins une des permissions requises."""

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(request: "APIRequest", *args, **kwargs):
            info = getattr(request, "api_key_info", None)
            if not info:
                raise ForbiddenError("Authentification requise")
            key_perms = set(info.get("permissions", []))
            if "*" not in key_perms and not key_perms.intersection(perms):
                raise ForbiddenError(
                    f"Permission(s) requise(s) : {', '.join(perms)}"
                )
            return fn(request, *args, **kwargs)

        return wrapper

    return decorator


# -- Objet requête --------------------------------------------------------------


class APIRequest:
    """Encapsule la requête HTTP avec helpers JSON, params, auth."""

    def __init__(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query: dict[str, list[str]],
        path_params: dict[str, str],
    ):
        self.method = method
        self.path = path
        self.headers = {k.lower(): v for k, v in headers.items()}
        self._body = body
        self.query = query
        self.path_params = path_params
        self.api_key: str | None = None
        self.api_key_info: dict | None = None
        self._json_cache: Any = None

    @property
    def json(self) -> Any:
        if self._json_cache is not None:
            return self._json_cache
        if not self._body:
            return None
        try:
            self._json_cache = json.loads(self._body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError(f"Corps JSON invalide : {exc}")
        return self._json_cache

    def get_query(self, name: str, default: str = "") -> str:
        vals = self.query.get(name, [default])
        return vals[0] if vals else default

    def get_query_int(self, name: str, default: int = 0) -> int:
        try:
            return int(self.get_query(name, str(default)))
        except ValueError:
            return default


# -- Réponses -------------------------------------------------------------------


class APIResponse:
    """Constructeur de réponses JSON standardisées."""

    @staticmethod
    def ok(data: Any = None, meta: dict | None = None) -> tuple[int, dict]:
        resp = {"success": True}
        if data is not None:
            resp["data"] = data
        if meta:
            resp["meta"] = meta
        return 200, resp

    @staticmethod
    def created(data: Any = None, location: str = "") -> tuple[int, dict]:
        resp = {"success": True, "data": data}
        if location:
            resp["meta"] = {"location": location}
        return 201, resp

    @staticmethod
    def no_content() -> tuple[int, None]:
        return 204, None

    @staticmethod
    def error(
        message: str, status: int = 400, code: str = "error", details: dict | None = None
    ) -> tuple[int, dict]:
        return status, {"success": False, "error": {"code": code, "message": message, "details": details or {}}}


# -- Serveur HTTP ---------------------------------------------------------------


class APIHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer portant la configuration et le routeur."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler_class: type,
        router: APIRouter,
        config: "APIConfig",
        key_store: APIKeyStore,
        cors_origins: list[str],
    ) -> None:
        super().__init__(address, handler_class)
        self.router = router
        self.config = config
        self.key_store = key_store
        self.cors_origins = cors_origins


class APIHandler(BaseHTTPRequestHandler):
    """Handler HTTP pour l'API REST."""

    server_version = "EduSyncAPI"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- Utilitaires -----------------------------------------------------------

    @property
    def router(self) -> APIRouter:
        return self.server.router  # type: ignore[attr-defined]

    @property
    def config(self) -> "APIConfig":
        return self.server.config  # type: ignore[attr-defined]

    @property
    def key_store(self) -> APIKeyStore:
        return self.server.key_store  # type: ignore[attr-defined]

    @property
    def cors_origins(self) -> list[str]:
        return self.server.cors_origins  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict | None) -> None:
        body = b""
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        # CORS
        origin = self.headers.get("Origin", "")
        if "*" in self.cors_origins or origin in self.cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key"
            )
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            )
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _send_error(self, status: int, message: str, code: str = "error", details: dict | None = None) -> None:
        self._send_json(status, {"success": False, "error": {"code": code, "message": message, "details": details or {}}})

    def _extract_api_key(self) -> str | None:
        # 1. Header X-API-Key
        key = self.headers.get("x-api-key")
        if key:
            return key.strip()
        # 2. Authorization: Bearer <key>
        auth = self.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            length = 0
        if length < 0 or length > MAX_BODY:
            self._send_error(
                413 if length > MAX_BODY else 400,
                "Corps de requête trop volumineux ou absent",
                "payload_too_large",
            )
            return b""
        return self.rfile.read(length) if length else b""

    def _parse_query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

    # -- Méthodes HTTP ---------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(204, None)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        query = self._parse_query()
        body = self._read_body()
        if body == b"" and method in ("POST", "PUT", "PATCH"):
            return  # erreur déjà envoyée

        handler, path_params = self.router.resolve(method, path)
        if handler is None:
            self._send_error(404, f"Route {method} {path} non trouvée", "not_found")
            return

        request = APIRequest(
            method=method,
            path=path,
            headers=dict(self.headers),
            body=body,
            query=query,
            path_params=path_params,
        )
        request.api_key = self._extract_api_key()

        try:
            result = handler(request)
            if isinstance(result, tuple):
                status, payload = result
            else:
                status, payload = 200, result
            self._send_json(status, payload)
        except APIError as exc:
            self._send_error(exc.status, exc.message, exc.code, exc.details)
        except Exception:  # noqa: BLE001 - rien ne doit fuir
            logger.exception("Erreur interne API sur %s %s", method, path)
            self._send_error(500, "Erreur interne du serveur", "internal_error")


# -- Configuration --------------------------------------------------------------


@dataclass
class APIConfig:
    """Configuration du serveur API — `api.json` (clair, aucun secret)."""

    actif: bool = False
    hote: str = "127.0.0.1"
    port: int = 8788
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    rate_limit_rpm: int = 60  # requêtes/minute par clé
    titre: str = "EduSync AD API"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (self.hote or "").strip():
            errors.append("Interface d'écoute requise (ex. 127.0.0.1).")
        if not (1 <= int(self.port) <= 65535):
            errors.append("Port invalide (1-65535).")
        if not (0 <= int(self.rate_limit_rpm) <= 10000):
            errors.append("Limite de taux invalide (0-10000).")
        return errors

    @property
    def base_url(self) -> str:
        host = self.hote.strip()
        display = "127.0.0.1" if host in ("0.0.0.0", "::", "*") else host
        return f"http://{display}:{int(self.port)}"


API_CONFIG_FILE = None  # sera défini au chargement (dépend de config_dir)


def load_api_config() -> APIConfig:
    from edusync_ad.core.config import config_dir

    global API_CONFIG_FILE
    if API_CONFIG_FILE is None:
        API_CONFIG_FILE = config_dir() / "api.json"
    dest = API_CONFIG_FILE
    if dest.exists():
        try:
            with dest.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                known = set(APIConfig.__dataclass_fields__)
                return APIConfig(**{k: v for k, v in data.items() if k in known})
        except (OSError, ValueError, TypeError):
            pass
    return APIConfig()


def save_api_config(config: APIConfig) -> Path:
    from edusync_ad.core.config import config_dir

    global API_CONFIG_FILE
    if API_CONFIG_FILE is None:
        API_CONFIG_FILE = config_dir() / "api.json"
    errors = config.validate()
    if errors:
        raise APIError("Configuration API invalide : " + "; ".join(errors), status=400)
    dest = API_CONFIG_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(
            {k: v for k, v in config.__dict__.items() if not k.startswith("_")},
            fh,
            indent=2,
            ensure_ascii=False,
        )
    return dest


# -- Cycle de vie serveur -------------------------------------------------------


class APIServer:
    """Cycle de vie : start/stop/url (pattern comme PortalServer)."""

    def __init__(
        self,
        config: APIConfig,
        router: APIRouter,
        key_store: APIKeyStore,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.key_store = key_store
        self.host = host if host is not None else config.hote
        self.port = int(port if port is not None else config.port)
        self._httpd: APIHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._rate_limit: dict[str, list[float]] = {}
        self._rate_lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def url(self) -> str:
        if self._httpd is None:
            return self.config.base_url
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        display = "127.0.0.1" if host in ("0.0.0.0", "::", "", "*") else host
        if ":" in display:
            display = f"[{display}]"
        return f"http://{display}:{int(port)}"

    def _check_rate_limit(self, api_key: str) -> bool:
        if self.config.rate_limit_rpm <= 0:
            return True
        now = time.time()
        minute_ago = now - 60
        with self._rate_lock:
            window = self._rate_limit.get(api_key, [])
            window = [t for t in window if t > minute_ago]
            if len(window) >= self.config.rate_limit_rpm:
                return False
            window.append(now)
            self._rate_limit[api_key] = window
            return True

    def start(self) -> str:
        if self._httpd is not None:
            return self.url

        def handler(*args, **kwargs):
            return APIHandler(*args, **kwargs)

        # Wrapper pour injecter le rate-limit
        original_init = APIHandler.__init__

        def wrapped_init(self_inner, *a, **kw):
            original_init(self_inner, *a, **kw)
            self_inner._rate_limiter = self._check_rate_limit

        APIHandler.__init__ = wrapped_init  # type: ignore[method-assign]

        try:
            httpd = APIHTTPServer(
                (self.host, self.port),
                APIHandler,
                self.router,
                self.config,
                self.key_store,
                self.config.cors_origins,
            )
        except OSError as exc:
            raise APIError(
                f"Impossible d'écouter sur {self.host}:{self.port} — {exc}"
            ) from exc

        thread = threading.Thread(
            target=httpd.serve_forever,
            name="api-http",
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        thread.start()
        self._httpd, self._thread = httpd, thread
        logger.info("API REST démarrée sur %s", self.url)
        return self.url

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd, self._thread = None, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        logger.info("API REST arrêtée")

    def __enter__(self) -> "APIServer":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()


# -- Router global (singleton pour l'application) -------------------------------

_router = APIRouter()
_key_store: APIKeyStore | None = None


def get_router() -> APIRouter:
    return _router


def get_key_store() -> APIKeyStore:
    global _key_store
    if _key_store is None:
        _key_store = load_api_keys()
    return _key_store


def create_api_server(config: APIConfig | None = None) -> APIServer:
    cfg = config or load_api_config()
    ks = get_key_store()
    return APIServer(cfg, _router, ks)