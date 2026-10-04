"""M27 — Serveur HTTP du portail auto-service (bibliothèque standard).

Le portail est servi à de futurs usagers (élèves, parents, personnels) par
``http.server`` : aucune dépendance externe, comme dans le reste du projet.

Routes
------

====== =============================================================
``/``           page d'accueil (trois opérations)
``/code``       demande d'un code de vérification (GET/POST)
``/verifier``   saisie du code, puis mot de passe (GET/POST)
``/demande``    demande de création de compte (GET/POST)
``/etat``       état du service au format JSON (diagnostic)
====== =============================================================

Partis pris de sécurité :

* tout contenu affiché passe par :func:`html.escape` (injection HTML) ;
* corps de requête limité à 16 Kio (``MAX_BODY``), sinon ``413`` ;
* aucune trace d'exception ni chemin interne ne quitte le serveur : les
  erreurs imprévues renvoient une page ``500`` générique et sont journalisées ;
* la version du serveur est figée (le banner ``Server:`` ne révèle ni Python
  ni la version de l'application) ;
* le serveur n'expose que la consultation : les écritures (validation d'une
  demande) se font depuis l'interface administrative.

Module indépendant de PyQt — testable avec ``urllib.request`` sur un port
éphémère.
"""

from __future__ import annotations

import json
import logging
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from edusync_ad.core.portal import (
    PROFIL_ELEVE,
    PROFIL_PERSONNEL,
    SCOPE_LOOKUP,
    SCOPE_RESET,
    PortalConfig,
    PortalError,
    PortalService,
)

logger = logging.getLogger("edusync_ad.portal.server")

#: Corps de requête maximal accepté (formulaire compris).
MAX_BODY = 16 * 1024

#: Libellés des deux portées de vérification.
SCOPE_LABELS = {
    SCOPE_RESET: "Réinitialiser mon mot de passe",
    SCOPE_LOOKUP: "Consulter mon identifiant",
}

PROFIL_LABELS = {PROFIL_ELEVE: "Élève", PROFIL_PERSONNEL: "Personnel"}


# -- Rendu HTML ---------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1rem; font-family: system-ui, -apple-system,
       "Segoe UI", Roboto, sans-serif; line-height: 1.5; background: #f4f6fb;
       color: #1c2333; }
main { max-width: 34rem; margin: 0 auto; background: #fff; padding: 1.75rem;
       border-radius: 12px; box-shadow: 0 6px 24px rgba(20, 30, 60, .08); }
h1 { font-size: 1.35rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 0 0 .75rem; }
.lead { margin-top: 0; color: #55607a; }
a { color: #1d4ed8; }
.cards { list-style: none; padding: 0; margin: 1rem 0 0; display: grid;
         gap: .75rem; }
.cards a { display: block; padding: .9rem 1rem; border: 1px solid #d7ddec;
           border-radius: 10px; text-decoration: none; font-weight: 600; }
.cards a:hover { border-color: #1d4ed8; background: #f0f4ff; }
.cards p { margin: .35rem 0 0; font-weight: 400; font-size: .9rem;
           color: #55607a; }
label { display: block; font-weight: 600; margin: .9rem 0 .3rem; }
input, select, textarea { width: 100%; padding: .55rem .65rem; font-size: 1rem;
                          border: 1px solid #c3cade; border-radius: 8px;
                          background: #fff; color: inherit; }
button { margin-top: 1.1rem; padding: .6rem 1.1rem; font-size: 1rem;
         font-weight: 600; color: #fff; background: #1d4ed8;
         border: 0; border-radius: 8px; cursor: pointer; }
button:hover { background: #1740b5; }
.msg { padding: .75rem .9rem; border-radius: 8px; margin: 1rem 0;
       font-size: .95rem; }
.msg.ok { background: #e7f7ec; border: 1px solid #a7ddba; color: #14603a; }
.msg.err { background: #fdecec; border: 1px solid #f0b4b4; color: #8a1f1f; }
.msg.info { background: #eef2ff; border: 1px solid #c3cbf5; color: #26327a; }
.hint { font-size: .85rem; color: #55607a; margin: .35rem 0 0; }
footer { max-width: 34rem; margin: 1rem auto 0; text-align: center;
         font-size: .85rem; color: #6b748c; }
footer a { color: inherit; }
dl { margin: .5rem 0 0; }
dt { font-size: .8rem; text-transform: uppercase; letter-spacing: .04em;
     color: #6b748c; margin-top: .75rem; }
dd { margin: .1rem 0 0; font-weight: 600; word-break: break-all; }
"""


def _layout(config: PortalConfig, title: str, body: str) -> str:
    """Gabarit commun à toutes les pages."""
    site = escape(config.titre or "Portail auto-service")
    page = escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="fr">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{page} — {site}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>{site}</h1>\n{body}\n</main>\n"
        f'<footer><a href="/">Retour à l\'accueil</a> · {site}</footer>\n'
        "</body>\n</html>\n"
    )


def _message(kind: str, text: str) -> str:
    return f'<p class="msg {kind}">{escape(text)}</p>'


def _home_body(config: PortalConfig) -> str:
    intro = escape(
        "Choisissez l'opération souhaitée. Un code de vérification vous sera "
        "envoyé à votre adresse de messagerie."
    )
    return (
        f'<p class="lead">{intro}</p>\n'
        '<ul class="cards">\n'
        f'<li><a href="/code?portee={SCOPE_RESET}">'
        f"{escape(SCOPE_LABELS[SCOPE_RESET])}</a>"
        "<p>Vous recevez un code par courriel, puis vous choisissez un nouveau "
        "mot de passe.</p></li>\n"
        f'<li><a href="/code?portee={SCOPE_LOOKUP}">'
        f"{escape(SCOPE_LABELS[SCOPE_LOOKUP])}</a>"
        "<p>Retrouvez votre identifiant et votre adresse de messagerie, après "
        "vérification par code.</p></li>\n"
        '<li><a href="/demande">Demander la création d\'un compte</a>'
        "<p>Votre demande est examinée par l'administrateur : les identifiants "
        "vous sont transmis une fois le compte créé.</p></li>\n"
        "</ul>\n"
        f'<p class="hint">Codes valides {int(config.code_minutes)} minutes, '
        f"{int(config.tentatives_max)} tentatives maximum.</p>"
    )


def _code_form_body(config: PortalConfig, scope: str, message: str = "", ticket: str = "") -> str:
    label = SCOPE_LABELS.get(scope, SCOPE_LABELS[SCOPE_RESET])
    parts = [
        f"<h2>{escape(label)}</h2>",
        f'<form method="post" action="/code">'
        f'<input type="hidden" name="portee" value="{escape(scope)}">',
        '<label for="identifiant">Identifiant ou adresse de messagerie</label>',
        '<input id="identifiant" name="identifiant" type="text" '
        'autocomplete="username" autocapitalize="none" spellcheck="false" '
        'autofocus required maxlength="254">',
        '<p class="hint">Le code est envoyé à l\'adresse de messagerie '
        "associée à ce compte.</p>",
        '<button type="submit">Envoyer le code</button>',
        "</form>",
    ]
    if message:
        parts.insert(1, _message("info", message))
    if ticket:
        parts.append(
            '<p class="msg ok">Un code vous attend : saisissez-le pour '
            "poursuivre.</p>"
            f'<a href="/verifier?ticket={escape(ticket)}&amp;portee={escape(scope)}">'
            "<button type=\"button\">Saisir le code reçu</button></a>"
        )
    return "\n".join(parts)


def _verifier_form_body(config: PortalConfig, scope: str, ticket: str) -> str:
    label = SCOPE_LABELS.get(scope, SCOPE_LABELS[SCOPE_RESET])
    parts = [
        f"<h2>{escape(label)}</h2>",
        '<form method="post" action="/verifier">'
        f'<input type="hidden" name="ticket" value="{escape(ticket)}">'
        f'<input type="hidden" name="portee" value="{escape(scope)}">',
        '<label for="code">Code reçu par courriel</label>',
        '<input id="code" name="code" type="text" inputmode="numeric" '
        'autocomplete="one-time-code" autofocus required maxlength="16"',
        'placeholder="000000" style="letter-spacing:.4em">',
    ]
    if scope == SCOPE_RESET:
        parts += [
            '<label for="mot_de_passe">Nouveau mot de passe</label>',
            '<input id="mot_de_passe" name="mot_de_passe" type="password" '
            'autocomplete="new-password" required>',
            '<label for="confirmation">Confirmation</label>',
            '<input id="confirmation" name="confirmation" type="password" '
            'autocomplete="new-password" required>',
            f'<p class="hint">{escape(_policy_hint(config))}</p>',
        ]
    parts += [
        '<button type="submit">Vérifier</button>',
        "</form>",
        f'<p class="hint"><a href="/code?portee={escape(scope)}">'
        "Demander un nouveau code</a></p>",
    ]
    return "\n".join(parts)


def _policy_hint(config: PortalConfig) -> str:
    return (
        f"Politique appliquée : {config.politique}. Le code reste valable si "
        "le mot de passe proposé ne convient pas."
    )


def _demande_body(config: PortalConfig) -> str:
    return (
        "<h2>Demande de création de compte</h2>"
        '<form method="post" action="/demande">'
        '<label for="profil">Profil</label>'
        '<select id="profil" name="profil">'
        f'<option value="{PROFIL_ELEVE}">Élève</option>'
        f'<option value="{PROFIL_PERSONNEL}">Personnel</option>'
        "</select>"
        '<label for="prenom">Prénom</label>'
        '<input id="prenom" name="prenom" type="text" required maxlength="64">'
        '<label for="nom">Nom</label>'
        '<input id="nom" name="nom" type="text" required maxlength="64">'
        '<label for="mail">Adresse de messagerie (facultative)</label>'
        '<input id="mail" name="mail" type="email" maxlength="254" '
        'autocomplete="email">'
        '<label for="classe">Classe ou service (facultatif)</label>'
        '<input id="classe" name="classe" type="text" maxlength="64">'
        '<label for="motif">Motif (facultatif)</label>'
        '<textarea id="motif" name="motif" rows="3" maxlength="500"></textarea>'
        '<button type="submit">Envoyer la demande</button>'
        "</form>"
        '<p class="hint">Aucun compte n\'est créé à cette étape : '
        "l'administrateur valide chaque demande depuis l'application.</p>"
    )


# -- Serveur ------------------------------------------------------------------


class PortalHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` portant la référence vers le service."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, service: PortalService) -> None:
        super().__init__(address, handler)
        self.service = service


class PortalHandler(BaseHTTPRequestHandler):
    """Routeur minimal : quatre pages et un état JSON."""

    #: Banner figé : ne révèle ni Python ni la version de l'application.
    server_version = "EduSyncPortail"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- Utilitaires ---------------------------------------------------------

    @property
    def service(self) -> PortalService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def config(self) -> PortalConfig:
        return self.service.config

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _html(self, status: int, body: str) -> None:
        self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))

    def _json(self, status: int, payload: dict) -> None:
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", blob)

    def _error_page(self, status: int, title: str, text: str, hint: str = "") -> None:
        body = f"<h2>{escape(title)}</h2>{_message('err', text)}"
        if hint:
            body += f'<p class="hint">{hint}</p>'
        self._html(status, _layout(self.config, title, body))

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

    @staticmethod
    def _field(form: dict[str, list[str]], name: str) -> str:
        values = form.get(name) or [""]
        return str(values[0]).strip()

    def _read_form(self) -> dict[str, list[str]] | None:
        """Lit un ``application/x-www-form-urlencoded`` borné à ``MAX_BODY``."""
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            length = -1
        if length < 0 or length > MAX_BODY:
            self._error_page(
                413 if length > MAX_BODY else 400,
                "Requête refusée",
                "Le formulaire envoyé est vide ou trop volumineux.",
            )
            return None
        body = self.rfile.read(length)
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type not in ("", "application/x-www-form-urlencoded"):
            self._error_page(415, "Format non pris en charge", "Formulaire attendu.")
            return None
        try:
            return parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        except (UnicodeDecodeError, ValueError):  # pragma: no cover - défensif
            self._error_page(400, "Requête invalide", "Formulaire illisible.")
            return None

    def _scope(self, form: dict[str, list[str]] | None = None) -> str:
        source = form if form is not None else self._query()
        value = self._field(source, "portee")
        return value if value in (SCOPE_RESET, SCOPE_LOOKUP) else SCOPE_RESET

    # -- Méthodes HTTP -------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - API de la bibliothèque
        self._dispatch(None)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(None)

    def do_POST(self) -> None:  # noqa: N802
        form = self._read_form()
        if form is None:
            return
        self._dispatch(form)

    def _dispatch(self, form: dict[str, list[str]] | None) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        handler: Callable[[dict[str, list[str]] | None], None]
        routes = {
            "/": self._page_home,
            "/code": self._page_code,
            "/verifier": self._page_verifier,
            "/demande": self._page_demande,
            "/etat": self._page_etat,
        }
        handler = routes.get(path, self._page_not_found)
        try:
            handler(form)
        except Exception:  # noqa: BLE001 - rien ne doit fuir vers le client
            logger.exception("Erreur interne du portail sur %s", path)
            self._error_page(
                500,
                "Erreur interne",
                "Le service a rencontré une erreur : réessayez dans un instant.",
            )

    # -- Pages ---------------------------------------------------------------

    def _page_not_found(self, form=None) -> None:
        self._error_page(
            404,
            "Page introuvable",
            "Cette adresse n'existe pas sur le portail.",
            '<a href="/">Retour à l\'accueil</a>',
        )

    def _page_home(self, form=None) -> None:
        body = _home_body(self.config)
        self._html(200, _layout(self.config, "Accueil", body))

    def _page_code(self, form: dict[str, list[str]] | None) -> None:
        scope = self._scope(form)
        if not form:
            self._html(200, _layout(self.config, SCOPE_LABELS[scope],
                                    _code_form_body(self.config, scope)))
            return
        message, ticket = self.service.request_code(
            self._field(form, "identifiant"), scope
        )
        self._html(
            200,
            _layout(
                self.config,
                SCOPE_LABELS[scope],
                _code_form_body(self.config, scope, message=message, ticket=ticket),
            ),
        )

    def _page_verifier(self, form: dict[str, list[str]] | None) -> None:
        scope = self._scope(form)
        query = form if form is not None else self._query()
        ticket = self._field(query, "ticket")
        if not ticket:
            self._error_page(
                400,
                "Code absent",
                "Demandez d'abord un code depuis la page d'accueil.",
                '<a href="/">Retour à l\'accueil</a>',
            )
            return

        if not form:
            self._html(
                200,
                _layout(self.config, SCOPE_LABELS[scope],
                        _verifier_form_body(self.config, scope, ticket)),
            )
            return

        code = self._field(form, "code")
        password = self._field(form, "mot_de_passe")
        confirmation = self._field(form, "confirmation")
        if scope == SCOPE_RESET and password != confirmation:
            self._html(
                400,
                _layout(
                    self.config,
                    SCOPE_LABELS[scope],
                    _message("err", "Les deux saisies du mot de passe diffèrent.")
                    + _verifier_form_body(self.config, scope, ticket),
                ),
            )
            return

        ok, message, infos = self.service.verify_code(
            ticket, code, scope=scope, new_password=password
        )
        if ok and infos:
            details = "".join(
                f"<dt>{escape(label)}</dt><dd>{escape(str(infos.get(key) or '—'))}</dd>"
                for key, label in (
                    ("nom", "Nom"),
                    ("identifiant", "Identifiant"),
                    ("mail", "Adresse de messagerie"),
                )
            )
            body = (
                "<h2>Vos identifiants</h2>"
                + _message("ok", message)
                + f"<dl>{details}</dl>"
                + '<p class="hint">Conservez ces informations : votre '
                "identifiant reste le même pendant toute votre scolarité.</p>"
            )
        elif ok:
            body = (
                "<h2>Mot de passe modifié</h2>"
                + _message("ok", message)
                + '<p class="hint">Utilisez votre nouvel identifiant et votre '
                "nouveau mot de passe pour vous connecter.</p>"
            )
        else:
            body = (
                "<h2>Vérification</h2>"
                + _message("err", message)
                + f'<p class="hint"><a href="/code?portee={escape(scope)}">'
                "Demander un nouveau code</a></p>"
            )
        self._html(200 if ok else 400, _layout(self.config, "Vérification", body))

    def _page_demande(self, form: dict[str, list[str]] | None) -> None:
        if not form:
            self._html(200, _layout(self.config, "Demande de compte", _demande_body(self.config)))
            return
        payload = {
            key: self._field(form, key)
            for key in ("profil", "prenom", "nom", "mail", "classe", "motif")
        }
        accepted, message = self.service.submit_request(payload)
        kind = "ok" if accepted else "err"
        body = _message(kind, message)
        if accepted:
            body += '<p class="hint">Vous pouvez fermer cette page.</p>'
        else:
            body += _demande_body(self.config)
        self._html(200 if accepted else 400,
                   _layout(self.config, "Demande de compte", body))

    def _page_etat(self, form=None) -> None:
        config = self.config
        try:
            attentes = len(self.service.list_requests())
        except Exception:  # noqa: BLE001 - diagnostic seulement
            attentes = -1
        self._json(
            200,
            {
                "service": "portail",
                "titre": config.titre,
                "actif": bool(config.actif),
                "base_url": config.base_url,
                "code_minutes": int(config.code_minutes),
                "tentatives_max": int(config.tentatives_max),
                "demandes_en_attente": attentes,
            },
        )


class PortalServer:
    """Cycle de vie du serveur : ``start`` / ``stop`` / ``url``.

    ``port=0`` demande un port éphémère (tests) ; ``url`` reflète alors le
    port réellement attribué par le système.
    """

    def __init__(self, service: PortalService, *, host: str | None = None,
                 port: int | None = None) -> None:
        self.service = service
        self.host = host if host is not None else service.config.hote
        self.port = int(port if port is not None else service.config.port)
        self._httpd: PortalHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def url(self) -> str:
        if self._httpd is None:
            return self.service.config.base_url
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        display = "127.0.0.1" if host in ("0.0.0.0", "::", "", "*") else host
        if ":" in display:
            display = f"[{display}]"
        return f"http://{display}:{int(port)}"

    def start(self) -> str:
        """Démarre le serveur dans un fil détaché. Retourne l'URL."""
        if self._httpd is not None:
            return self.url
        try:
            httpd = PortalHTTPServer((self.host, self.port), PortalHandler, self.service)
        except OSError as exc:
            raise PortalError(
                f"Impossible d'écouter sur {self.host}:{self.port} — {exc}"
            ) from exc
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="portail-http",
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        thread.start()
        self._httpd, self._thread = httpd, thread
        logger.info("Portail auto-service démarré sur %s", self.url)
        return self.url

    def stop(self) -> None:
        """Arrête le serveur (idempotent)."""
        httpd, thread = self._httpd, self._thread
        self._httpd, self._thread = None, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        logger.info("Portail auto-service arrêté")

    def __enter__(self) -> "PortalServer":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
