"""Client Microsoft Graph / Microsoft Entra ID (M19).

Connexion via « app registration » (identifiant client) en OAuth2
*client_credentials* avec deux modes d'authentification :

- **Secret client** — ``client_secret`` classique
- **Certificat** — ``client_assertion`` JWT signé RS256 (clé + certificat PEM)

Le secret est chiffré sur disque (AES-GCM, clé locale via ``crypto``), jamais
stocké en clair. Toutes les requêtes passent par :meth:`GraphClient.request`
qui centralise le jeton d'accès (mise en cache), la pagination ``@odata.nextLink``
et la traduction des erreurs Graph en :class:`GraphError` (dont les limites
de débit 429).

Réseau : ``urllib`` uniquement — aucune dépendance externe (cohérent avec
``core/updater.py``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from edusync_ad.core.config import config_dir
from edusync_ad.core.crypto import decrypt_str, encrypt_str, get_or_create_key

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
LOGIN_BASE_URL = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

M365_CONFIG_FILE = config_dir() / "m365.json"
DEFAULT_TIMEOUT = 25
TOKEN_MARGIN = 60  # rafraîchir le jeton 60 s avant expiration

USER_FIELDS = (
    "id,userPrincipalName,displayName,givenName,surname,"
    "mail,accountEnabled,usageLocation"
)
GROUP_FIELDS = "id,displayName,mailNickname,mail,groupTypes,securityEnabled"


class GraphError(Exception):
    """Erreur renvoyée par Microsoft Graph ou par le point de connexion OAuth2."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class GraphConfig:
    """Paramètres de connexion à Microsoft Entra ID (app registration)."""

    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    auth_mode: str = "secret"          # "secret" | "certificate"
    certificate_path: str = ""         # PEM : clé privée (+ certificat)
    usage_location: str = "FR"         # requis avant attribution de licence

    @property
    def token_url(self) -> str:
        return f"{LOGIN_BASE_URL}/{self.tenant_id}/oauth2/v2.0/token"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.tenant_id.strip():
            errors.append("L'identifiant de locataire (tenant ID) est vide.")
        elif len(self.tenant_id.strip()) != 36:
            errors.append(
                "Le tenant ID doit être un GUID (ex : 00000000-0000-0000-0000-000000000000)."
            )
        if not self.client_id.strip():
            errors.append("L'identifiant d'application (client ID) est vide.")
        if self.auth_mode == "certificate":
            if not self.certificate_path.strip():
                errors.append("Le chemin du fichier de certificat (PEM) est vide.")
            elif not Path(self.certificate_path).expanduser().exists():
                errors.append(f"Fichier introuvable : {self.certificate_path}")
        elif not self.client_secret.strip():
            errors.append("Le secret client est vide.")
        if len(self.usage_location.strip()) != 2:
            errors.append("La localisation d'usage doit être un code pays ISO 2 (ex : FR).")
        return errors

    def to_dict(self, *, include_secret: bool = False) -> dict:
        data = {
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "auth_mode": self.auth_mode,
            "certificate_path": self.certificate_path,
            "usage_location": self.usage_location,
        }
        if include_secret:
            data["client_secret_enc"] = _seal_secret(self.client_secret)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "GraphConfig":
        return cls(
            tenant_id=data.get("tenant_id", ""),
            client_id=data.get("client_id", ""),
            client_secret=_unseal_secret(data.get("client_secret_enc", "")),
            auth_mode=data.get("auth_mode", "secret"),
            certificate_path=data.get("certificate_path", ""),
            usage_location=data.get("usage_location", "FR"),
        )


# -- Chiffrement du secret client ---------------------------------------------------

def _seal_secret(secret: str) -> str:
    if not secret:
        return ""
    try:
        return encrypt_str(get_or_create_key(), secret)
    except Exception:  # noqa: BLE001 — clé/locale indisponible : jamais bloquant
        return ""


def _unseal_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return decrypt_str(get_or_create_key(), token)
    except Exception:  # noqa: BLE001 — clé régénérée : secret à ressaisir
        return ""


def load_m365_config() -> GraphConfig:
    if M365_CONFIG_FILE.exists():
        try:
            with M365_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                return GraphConfig.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError):
            pass
    return GraphConfig()


def save_m365_config(config: GraphConfig) -> None:
    M365_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with M365_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(include_secret=True), fh, indent=2, ensure_ascii=False)


# -- Client ----------------------------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _error_from_payload(payload: bytes, status: int) -> GraphError:
    message, code = "", ""
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
        err = data.get("error", data) if isinstance(data, dict) else {}
        if isinstance(err, dict):
            message = str(err.get("message", "") or "")
            code = str(err.get("code", "") or "")
    except ValueError:
        pass
    if not message and payload:
        message = payload[:300].decode("utf-8", "replace")
    if status == 429:
        message = f"Limite de débit Graph atteinte (429) — réessayez plus tard. {message}"
    elif status in (401, 403):
        message = (
            "Accès refusé — vérifiez les permissions (Application) de l'app "
            f"registration et les rôles attribués. {message}"
        )
    return GraphError(message.strip() or f"Erreur Graph (HTTP {status}).", status, code)


class GraphClient:
    """Accès à Microsoft Graph avec jeton mis en cache.

    ``opener`` est injectable pour les tests : callable signature identique à
    ``urllib.request.urlopen`` (retourne un gestionnaire de contexte avec
    ``read()``, ``status`` et ``headers``).
    """

    def __init__(
        self,
        config: GraphConfig,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._opener = opener or urlopen
        self._timeout = timeout
        self._now = now
        self._token: str = ""
        self._token_expires_at: float = 0.0

    # -- Authentification --------------------------------------------------------------

    def _client_assertion(self) -> str:
        """JWT RS256 (``client_assertion``) pour l'authentification par certificat."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pem_path = Path(self.config.certificate_path).expanduser()
        try:
            raw = pem_path.read_bytes()
            key = serialization.load_pem_private_key(raw, password=None)
        except (OSError, ValueError) as exc:
            raise GraphError(f"Certificat/clé PEM illisible : {exc}") from exc

        header: dict[str, str] = {"alg": "RS256", "typ": "JWT"}
        try:
            cert = x509.load_pem_x509_certificate(raw)
            thumb = cert.fingerprint(hashes.SHA1())
            header["x5t"] = _b64url(thumb)
        except ValueError:
            pass  # PEM sans certificat : AAD s'appuie alors sur l'empreinte enregistrée

        now = int(self._now())
        payload = {
            "aud": self.config.token_url,
            "iss": self.config.client_id,
            "sub": self.config.client_id,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "nbf": now,
            "exp": now + 600,
        }
        signing_input = (
            f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
            f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
        ).encode()
        try:
            signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:  # noqa: BLE001
            raise GraphError(f"Échec de signature du jeton : {exc}") from exc
        return f"{signing_input.decode()}.{_b64url(signature)}"

    def _token_data(self) -> dict[str, str]:
        data = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "scope": GRAPH_SCOPE,
        }
        if self.config.auth_mode == "certificate":
            data["client_assertion_type"] = CLIENT_ASSERTION_TYPE
            data["client_assertion"] = self._client_assertion()
        else:
            data["client_secret"] = self.config.client_secret
        return data

    def get_token(self, *, force: bool = False) -> str:
        if not force and self._token and self._now() < self._token_expires_at - TOKEN_MARGIN:
            return self._token

        errors = self.config.validate()
        if errors:
            raise GraphError("Configuration incomplète :\n- " + "\n- ".join(errors))

        body = urlencode(self._token_data()).encode()
        req = Request(
            self.config.token_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        status, payload = self._call(req)
        if status >= 400:
            raise _error_from_payload(payload, status)
        try:
            data = json.loads(payload.decode("utf-8"))
        except ValueError as exc:
            raise GraphError("Réponse d'authentification illisible.") from exc
        token = data.get("access_token")
        if not token:
            raise GraphError("Aucun jeton d'accès retourné par Microsoft.")
        self._token = str(token)
        self._token_expires_at = self._now() + float(data.get("expires_in", 3600))
        return self._token

    # -- Appel HTTP -----------------------------------------------------------------------

    def _call(self, req: Request) -> tuple[int, bytes]:
        try:
            with self._opener(req, timeout=self._timeout) as resp:
                return int(getattr(resp, "status", 200) or 200), resp.read()
        except HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:  # noqa: BLE001
                payload = b""
            return exc.code, payload
        except URLError as exc:
            raise GraphError(f"Connexion impossible : {exc.reason}") from exc
        except OSError as exc:
            raise GraphError(f"Erreur réseau : {exc}") from exc

    def request(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        raw: bytes | None = None,
        content_type: str = "application/json",
        absolute: bool = False,
    ) -> Any:
        """Requête authentifiée vers Graph. Retourne le JSON décodé ({} si vide)."""
        url = path if absolute else f"{GRAPH_BASE_URL}{path}"
        body = raw if raw is not None else (
            json.dumps(data).encode() if data is not None else None
        )
        headers = {
            "Authorization": f"Bearer {self.get_token()}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = content_type
        req = Request(url, data=body, method=method.upper(), headers=headers)
        status, payload = self._call(req)
        if status >= 400:
            raise _error_from_payload(payload, status)
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError as exc:
            raise GraphError(
                f"Réponse JSON illisible (HTTP {status})."
            ) from exc

    def _get_all(self, path: str) -> list[dict]:
        results: list[dict] = []
        url = f"{GRAPH_BASE_URL}{path}"
        while url:
            page = self.request("GET", url, absolute=True)
            value = page.get("value", []) if isinstance(page, dict) else []
            results.extend(v for v in value if isinstance(v, dict))
            url = page.get("@odata.nextLink") if isinstance(page, dict) else None
        return results

    # -- Organisation / utilisateurs ---------------------------------------------------------

    def organization(self) -> dict:
        data = self.request("GET", "/organization")
        value = data.get("value", []) if isinstance(data, dict) else []
        return value[0] if value else {}

    def list_users(self) -> list[dict]:
        return self._get_all(f"/users?$select={USER_FIELDS}&$top=999")

    def get_user(self, user_id_or_upn: str) -> dict:
        return self.request("GET", f"/users/{user_id_or_upn}?$select={USER_FIELDS}")

    def create_user(
        self,
        *,
        user_principal_name: str,
        display_name: str,
        mail_nickname: str,
        password: str,
        given_name: str = "",
        surname: str = "",
        account_enabled: bool = True,
        usage_location: str = "FR",
        force_change_password: bool = False,
    ) -> dict:
        payload = {
            "accountEnabled": account_enabled,
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "userPrincipalName": user_principal_name,
            "usageLocation": usage_location,
            "passwordProfile": {
                "password": password,
                "forceChangePasswordNextSignIn": force_change_password,
            },
        }
        if given_name:
            payload["givenName"] = given_name
        if surname:
            payload["surname"] = surname
        return self.request("POST", "/users", data=payload)

    def delete_user(self, user_id: str) -> None:
        self.request("DELETE", f"/users/{user_id}")

    # -- Groupes ------------------------------------------------------------------------------

    def list_groups(self) -> list[dict]:
        return self._get_all(f"/groups?$select={GROUP_FIELDS}&$top=999")

    def create_group(
        self,
        *,
        display_name: str,
        mail_nickname: str,
        description: str = "",
        kind: str = "m365",
    ) -> dict:
        """kind : ``m365`` (groupe Microsoft 365, base d'un Team),
        ``distribution`` ou ``security``."""
        payload: dict[str, Any] = {
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "mailEnabled": kind != "security",
            "securityEnabled": kind == "security",
            "groupTypes": ["Unified"] if kind == "m365" else [],
        }
        if description:
            payload["description"] = description
        return self.request("POST", "/groups", data=payload)

    def list_group_members(self, group_id: str) -> list[dict]:
        return self._get_all(
            f"/groups/{group_id}/members?$select=id,userPrincipalName,displayName&$top=999"
        )

    def add_group_member(self, group_id: str, member_id: str) -> None:
        self.request(
            "POST",
            f"/groups/{group_id}/members/$ref",
            data={"@odata.id": f"{GRAPH_BASE_URL}/users/{member_id}"},
        )

    def remove_group_member(self, group_id: str, member_id: str) -> None:
        self.request("DELETE", f"/groups/{group_id}/members/{member_id}/$ref")

    def create_team(self, group_id: str) -> None:
        """Crée l'équipe Teams rattachée au groupe Microsoft 365.

        Opération asynchrone côté Microsoft (202 Accepted) : le groupe est la
        source de vérité, l'équipe finit de se provisionner en arrière-plan.
        """
        self.request(
            "PUT",
            f"/groups/{group_id}/team",
            data={"memberSettings": {"allowCreateUpdateChannels": True}},
        )

    # -- Licences -------------------------------------------------------------------------------

    def subscribed_skus(self) -> dict[str, str]:
        """Mappe ``skuPartNumber`` → ``skuId`` des licences souscrites."""
        result: dict[str, str] = {}
        for sku in self._get_all("/subscribedSkus"):
            part = sku.get("skuPartNumber")
            sku_id = sku.get("skuId")
            if part and sku_id:
                result[str(part)] = str(sku_id)
        return result

    def assign_license(self, user_id: str, sku_id: str) -> dict:
        return self.request(
            "POST",
            f"/users/{user_id}/assignLicense",
            data={"addLicenses": [{"skuId": sku_id}], "removeLicenses": []},
        )

    def remove_license(self, user_id: str, sku_id: str) -> dict:
        return self.request(
            "POST",
            f"/users/{user_id}/assignLicense",
            data={"addLicenses": [], "removeLicenses": [sku_id]},
        )

    # -- Photos ------------------------------------------------------------------------------------

    def upload_user_photo(self, user_id: str, jpeg_bytes: bytes) -> None:
        """Dépose la photo de profil (JPEG, ≤ 4 Mo)."""
        self.request(
            "PUT",
            f"/users/{user_id}/photo/$content",
            raw=jpeg_bytes,
            content_type="image/jpeg",
        )
