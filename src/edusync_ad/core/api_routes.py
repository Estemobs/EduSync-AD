"""M28 — Itinérants REST de l'API EduSync AD.

Ce module enregistre toutes les routes REST dans le `APIRouter`.
Les handlers renvoient des tuples ``(status_code, payload_dict)`` ou lèvent
``APIError`` (qui sera traduit par le handler HTTP en JSON).

Chaque route applique un garde-fou par défaut via le décorateur
``require_api_key`` posé dans le handler principal (``ui/api_page.py`` ou
``app.py``).
"""

from __future__ import annotations

from typing import Any

from edusync_ad.core.api import (
    APIRouter,
    APIError,
    NotFoundError,
    UnauthorizedError,
    ForbiddenError,
    ValidationError,
    APIResponse,
    require_api_key,
    APIKeyStore,
)
from edusync_ad.core.api_auth import WebhookEvent, WebhookStore, WebhookDispatcher

# ---------------------------------------------------------------------------
# Helpers JSON / sérialisation
# ---------------------------------------------------------------------------


def _json_response(data: Any, status: int = 200) -> tuple[int, dict[str, Any]]:
    return status, {"success": True, "data": data}


def _error_response(
    message: str,
    status: int,
    code: str = "error",
    details: dict | None = None,
) -> tuple[int, dict[str, Any]]:
    return status, {"success": False, "error": {"code": code, "message": message, "details": details or {}}}


# ---------------------------------------------------------------------------
# Modules / helpers internes (mockés pour l'instant)
# ---------------------------------------------------------------------------

from edusync_ad.core.config import AppConfig
from edusync_ad.core.portal import PortalService, PortalConfig, PortalStore, PortalUser, Demande


# -- Authentification simplifiée (pour le serveur API) ----------------------

# Une seule clé par défaut pour le serveur autonome (M28 démo).
# En production, on utilise `APIKeyStore`.
DEFAULT_API_KEY = "edusync_admin_default_change_me"


def _check_api_key(request) -> None:
    """Vérification simple : clé dans header ou None → 401."""
    key = request.headers.get("x-api-key")
    if not key or key != DEFAULT_API_KEY:
        raise UnauthorizedError("Clé API invalide ou absente")


# ---------------------------------------------------------------------------
# Routes — Utilisateurs
# ---------------------------------------------------------------------------

router = APIRouter()


# GET /api/v1/users{?q=}&{page}&{page_size}
@router.get("/users")
@router.get("/api/v1/users")
def list_users(request) -> tuple[int, dict]:
    _check_api_key(request)
    q = request.get_query("q", "")
    page = request.get_query_int("page", 1)
    page_size = request.get_query_int("page_size", 50)
    # TODO : implémenter vrai filtrage AD + pagination
    users = [
        {"sam": "toto", "cn": "Toto Tot", "mail": "toto@lycee.local"},
        {"sam": "jean.dupont", "cn": "Jean Dupont", "mail": "jean@lycee.local"},
    ]
    return _json_response(
        {
            "items": users[(page - 1) * page_size : page * page_size],
            "total": len(users),
            "page": page,
            "page_size": page_size,
        }
    )


# GET /api/v1/users/{sam}
@router.get("/users/{sam}")
@router.get("/api/v1/users/{sam}")
def get_user(request) -> tuple[int, dict]:
    _check_api_key(request)
    sam = request.path_params["sam"]
    # TODO : vrai query AD
    user = {"sam": sam, "cn": f"{sam.title()}", "mail": f"{sam.lower()}@lycee.local"}
    return _json_response(user)


# POST /api/v1/users
@router.post("/users")
@router.post("/api/v1/users")
def create_user(request) -> tuple[int, dict]:
    _check_api_key(request)
    data = request.json
    if not data or not data.get("sam") or not data.get("mail"):
        raise ValidationError("sam et mail obligatoires", details={"received": data})
    # TODO : vrai create_user AD
    return _json_response(
        {"sam": data["sam"], "cn": data.get("cn", data["sam"]), "mail": data["mail"]},
        status=201,
    )


# PATCH /api/v1/users/{sam}
@router.patch("/users/{sam}")
@router.patch("/api/v1/users/{sam}")
def update_user(request) -> tuple[int, dict]:
    _check_api_key(request)
    data = request.json
    sam = request.path_params["sam"]
    # TODO : vrai update AD
    return _json_response({"sam": sam, "updated": data})


# DELETE /api/v1/users/{sam}
@router.delete("/users/{sam}")
@router.delete("/api/v1/users/{sam}")
def delete_user(request) -> tuple[int, dict]:
    _check_api_key(request)
    sam = request.path_params["sam"]
    # TODO : vrai delete AD
    return _json_response({"sam": sam, "deleted": True})


# -- Routes — Groupes --------------------------------------------------------

# GET /api/v1/groups{?q=}&{page}&{page_size}
@router.get("/groups")
@router.get("/api/v1/groups")
def list_groups(request) -> tuple[int, dict]:
    _check_api_key(request)
    q = request.get_query("q", "")
    page = request.get_query_int("page", 1)
    page_size = request.get_query_int("page_size", 50)
    groups = [
        {"cn": "Admins", "sam": "admins", "mail": "admins@lycee.local"},
        {"cn": "Élèves-3e", "sam": "eleve-3e", "mail": "3e@lycee.local"},
    ]
    return _json_response(
        {"items": groups[(page - 1) * page_size : page * page_size], "total": len(groups)}
    )


# GET /api/v1/groups/{cn}
@router.get("/groups/{cn}")
@router.get("/api/v1/groups/{cn}")
def get_group(request) -> tuple[int, dict]:
    _check_api_key(request)
    cn = request.path_params["cn"]
    group = {"cn": cn, "sam": f"group_{cn}", "mail": f"{cn.lower()}@lycee.local"}
    return _json_response(group)


# POST /api/v1/groups
@router.post("/groups")
@router.post("/api/v1/groups")
def create_group(request) -> tuple[int, dict]:
    _check_api_key(request)
    data = request.json
    if not data or not data.get("cn"):
        raise ValidationError("cn requis", details={"received": data})
    # TODO : vrai create_group AD
    return _json_response({"cn": data["cn"], "created": True}, status=201)


# -- Routes — OUs -------------------------------------------------------------

# GET /api/v1/ous{?q=}&{page}&{page_size}
@router.get("/ous")
@router.get("/api/v1/ous")
def list_ous(request) -> tuple[int, dict]:
    _check_api_key(request)
    q = request.get_query("q", "")
    page = request.get_query_int("page", 1)
    page_size = request.get_query_int("page_size", 50)
    ous = [{"dn": "ou=Eleves,dc=lycee,dc=local", "name": "Élèves"}, {"dn": "ou=Personnel,dc=lycee,dc=local", "name": "Personnel"}]
    return _json_response(
        {
            "items": ous[(page - 1) * page_size : page * page_size],
            "total": len(ous),
            "page": page,
            "page_size": page_size,
        }
    )


# GET /api/v1/ous/{dn}
@router.get("/ous/{dn}")
@router.get("/api/v1/ous/{dn}")
def get_ou(request) -> tuple[int, dict]:
    _check_api_key(request)
    dn = request.path_params["dn"]
    ou = {"dn": dn, "name": dn.split("=")[1].split(",")[0].title()}
    return _json_response(ou)


# -- Routes — Demandes portail -------------------------------------------------

# GET /api/v1/portal/requests{?statut=}
@router.get("/portal/requests")
@router.get("/api/v1/portal/requests")
def list_portal_requests(request) -> tuple[int, dict]:
    _check_api_key(request)
    statut = request.get_query("statut") or ""
    # TODO : vrai query store
    demandes = [
        {
            "id": "d1",
            "profil": "eleve",
            "prenom": "Léa",
            "nom": "Durand",
            "mail": "lea@lycee.local",
            "classe": "3eA",
            "motif": "Nouvel élève",
            "statut": "en_attente",
            "created_at": "2026-09-15T10:30:00Z",
        }
    ]
    return _json_response(
        {"items": demandes, "total": 1, "statut": statut}
    )


# POST /api/v1/portal/requests/{demande_id}/approve
@router.post("/portal/requests/{demande_id}/approve")
@router.post("/api/v1/portal/requests/{demande_id}/approve")
def approve_portal_request(request) -> tuple[int, dict]:
    _check_api_key(request)
    demande_id = request.path_params["demande_id"]
    data = request.json or {}
    notify = data.get("notify", True)
    # TODO : vrai approve_request
    return _json_response(
        {
            "demande_id": demande_id,
            "approved": True,
            "notify": notify,
            "infos": {
                "identifiant": f"lea{secrets.token_hex(3).upper()}",
                "mot_de_passe": "P@ssw0rdGeneré",
                "mail": "lea@lycee.local",
            },
        }
    )


# POST /api/v1/portal/requests/{demande_id}/refuse
@router.post("/portal/requests/{demande_id}/refuse")
@router.post("/api/v1/portal/requests/{demande_id}/refuse")
def refuse_portal_request(request) -> tuple[int, dict]:
    _check_api_key(request)
    demande_id = request.path_params["demande_id"]
    data = request.json or {}
    note = data.get("note", "")
    # TODO : vrai refuse_request
    return _json_response({"demande_id": demande_id, "refused": True, "note": note})


# -- Routes — Webhooks --------------------------------------------------------

# GET /api/v1/webhooks{?active=}
@router.get("/webhooks")
@router.get("/api/v1/webhooks")
def list_webhooks(request) -> tuple[int, dict]:
    _check_api_key(request)
    active = request.get_query("active") == "true"
    # TODO : vrai charge depuis store
    whs = [
        {
            "id": "w1",
            "name": "Serveur LDAP events",
            "url": "https://mon-serveur.local/webhooks",
            "events": ["user.created", "user.updated"],
            "active": True,
        }
    ]
    return _json_response(whs)


# POST /api/v1/webhooks
@router.post("/webhooks")
@router.post("/api/v1/webhooks")
def create_webhook(request) -> tuple[int, dict]:
    _check_api_key(request)
    data = request.json
    if not data or not data.get("name") or not data.get("url"):
        raise ValidationError("name et url requis", details={"received": data})
    # TODO : vrai create
    wh_id = f"w{secrets.token_hex(4)}"
    return _json_response(
        {"id": wh_id, "name": data["name"], "url": data["url"], "created": True}, status=201
    )


# POST /api/v1/webhooks/{webhook_id}/test
@router.post("/webhooks/{webhook_id}/test")
@router.post("/api/v1/webhooks/{webhook_id}/test")
def test_webhook(request) -> tuple[int, dict]:
    _check_api_key(request)
    webhook_id = request.path_params["webhook_id"]
    # TODO : vrai test
    return _json_response({"webhook_id": webhook_id, "tested": True})


# -- Routes — Audit ----------------------------------------------------------

# GET /api/v1/audit{?date_debut}&{date_fin}&{action}&{resultat}&{page}
@router.get("/audit")
@router.get("/api/v1/audit")
def list_audit(request) -> tuple[int, dict]:
    _check_api_key(request)
    date_debut = request.get_query("date_debut")
    date_fin = request.get_query("date_fin")
    action = request.get_query("action")
    resultat = request.get_query("resultat")
    # TODO : vrai query audit.db
    entries = [
        {
            "id": "a1",
            "action": "user.created",
            "utilisateur": "admin",
            "resultat": "succes",
            "compte": "toto",
            "domaine": "lycee.local",
            "detail": "Création de toto@lycee.local",
            "timestamp": "2026-09-15T10:30:00Z",
        }
    ]
    return _json_response(
        {"items": entries, "total": 1, "filtres": {"date_debut": date_debut, "date_fin": date_fin, "action": action, "resultat": resultat}}
    )


# ---------------------------------------------------------------------------
# Routes API statiques (documentation, état, health)
# ---------------------------------------------------------------------------

# GET /api/v1/health
@router.get("/health")
@router.get("/api/v1/health")
def health(request) -> tuple[int, dict]:
    _check_api_key(request)
    return _json_response(
        {
            "status": "ok",
            "service": "edusync-ad-api",
            "version": "1.15.0-dev",
            "api_version": API_VERSION,
        }
    )


# GET /api/v1/info
@router.get("/info")
@router.get("/api/v1/info")
def info(request) -> tuple[int, dict]:
    _check_api_key(request)
    return _json_response(
        {
            "name": "EduSync AD",
            "version": "1.15.0",
            "api_version": API_VERSION,
            "endpoints": {
                "users": "/api/v1/users",
                "user": "/api/v1/users/{{sam}}",
                "groups": "/api/v1/groups",
                "groups_single": "/api/v1/groups/{{cn}}",
                "ous": "/api/v1/ous",
                "portal_requests": "/api/v1/portal/requests",
                "portal_approve": "/api/v1/portal/requests/{{demande_id}}/approve",
                "portal_refuse": "/api/v1/portal/requests/{{demande_id}}/refuse",
                "webhooks": "/api/v1/webhooks",
                "health": "/api/v1/health",
            },
        }
    )


# Export du router vers le handler principal
__all__ = ["router"]