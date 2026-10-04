"""M28 — Spécification OpenAPI (bibliothèque standard, génération statique).

Ce module génère un document OpenAPI 3.0.3 conforme à la version ``API_VERSION = "v1"``.
Il n'utilise **aucune dépendance externe** (pas de ``fastapi``, pas de ``drf``) :
tout est du texte JSON/YAML produit par des fonctions pures à partir des ``router``
enregistrés et de la ``APIKeyStore``.

Le document généré peut être :
* servi ``/openapi.json`` via l'API elle-même (route ``GET /openapi.json``) ;
* écrit sur disque ``api-spec.json`` pour affichage dans des outils (Swagger UI,
  Redoc, Insomnia, Postman) ;
* servir de base à des stubs de serveurs (Python, TypeScript, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

API_VERSION = "v1"
API_TITLE = "EduSync AD REST API"
API_DESCRIPTION = (
    "API REST pour la gestion du cycle de vie des comptes Active Directory "
    "— établissement scolaire, PME, collectivités. "
    "Architecture stdlib (``http.server``, ``sqlite3``) — aucune dépendance externe."
)
API_CONTACT_NAME = "Équipe EduSync AD"
API_CONTACT_EMAIL = "contact@edusync-ad.example"
API_LICENSE_NAME = "MIT"
API_LICENSE_URL = "https://opensource.org/licenses/MIT"
API_SERVERS = [{"url": "/", "description": "API locale"}]


class OpenAPISpec:
    """Structure du document OpenAPI (en mémoire, sérialisable JSON)."""

    def __init__(self) -> None:
        self.openapi = "3.0.3"
        self.info = {
            "title": API_TITLE,
            "description": API_DESCRIPTION,
            "version": "1.0.0",
            "contact": {"name": API_CONTACT_NAME, "email": API_CONTACT_EMAIL},
            "license": {"name": API_LICENSE_NAME, "url": API_LICENSE_URL},
        }
        self.servers = API_SERVERS
        self.paths: dict[str, Any] = {}
        self.components: dict[str, Any] = {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "Clé API EduSync AD (header X-API-Key)",
                }
            }
        }
        self.security: list[Any] = [{"ApiKeyAuth": []}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "openapi": self.openapi,
            "info": self.info,
            "servers": self.servers,
            "paths": self.paths,
            "components": self.components,
            "security": self.security,
        }


def generate_spec(router: Any, title: str = API_TITLE, version: str = API_VERSION) -> OpenAPISpec:
    """Génère un ``OpenAPISpec`` à partir du ``APIRouter`` enregistré."""

    spec = OpenAPISpec()

    # Chemins définis explicitement pour l'API M28.
    manual_paths = {
        f"/api/{API_VERSION}/users": {
            "get": {
                "summary": "Liste des utilisateurs",
                "description": "Retourne la liste paginée des utilisateurs du domaine AD.",
                "parameters": [
                    {"name": "q", "in": "query", "description": "Filtre par SAM ou mail"},
                    {"name": "page", "in": "query", "description": "Numéro de page", "default": 1},
                    {"name": "page_size", "in": "query", "description": "Éléments par page", "default": 50},
                ],
                "responses": {
                    "200": {"description": "Liste paginée d'utilisateurs"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/users/{{sam}}": {
            "get": {
                "summary": "Récupération d'un utilisateur",
                "description": "Retourne les détails d'un utilisateur identifié par son SAM.",
                "parameters": [{"name": "sam", "in": "path", "description": "Identifiant SAM de l'utilisateur", "required": True}],
                "responses": {
                    "200": {"description": "Détails de l'utilisateur"},
                    "404": {"description": "Utilisateur introuvable"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/users": {
            "post": {
                "summary": "Création d'un utilisateur",
                "description": "Crée un nouvel utilisateur dans l'annuaire Active Directory.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["sam", "mail"],
                                "properties": {
                                    "sam": {"type": "string", "description": "Identifiant SAM"},
                                    "mail": {"type": "string", "format": "email", "description": "Adresse de messagerie"},
                                    "cn": {"type": "string", "description": "Nom complet ou CN"},
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "201": {"description": "Utilisateur créé"},
                    "400": {"description": "Données invalides"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/groups": {
            "get": {
                "summary": "Liste des groupes",
                "description": "Retourne la liste paginée des groupes du domaine AD.",
                "parameters": [
                    {"name": "q", "in": "query", "description": "Filtre par CN"},
                    {"name": "page", "in": "query", "description": "Numéro de page", "default": 1},
                    {"name": "page_size", "in": "query", "description": "Éléments par page", "default": 50},
                ],
                "responses": {
                    "200": {"description": "Liste paginée de groupes"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/groups/{{cn}}": {
            "get": {
                "summary": "Récupération d'un groupe",
                "description": "Retourne les détails d'un groupe identifié par son CN.",
                "parameters": [{"name": "cn", "in": "path", "description": "Nom complet du groupe", "required": True}],
                "responses": {
                    "200": {"description": "Détails du groupe"},
                    "404": {"description": "Groupe introuvable"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/groups": {
            "post": {
                "summary": "Création d'un groupe",
                "description": "Crée un nouveau groupe dans l'annuaire Active Directory.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["cn"],
                                "properties": {
                                    "cn": {"type": "string", "description": "Nom du groupe"},
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "201": {"description": "Groupe créé"},
                    "400": {"description": "Données invalides"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/ous": {
            "get": {
                "summary": "Liste des OU",
                "description": "Retourne la liste des Organizational Units du domaine.",
                "parameters": [
                    {"name": "q", "in": "query", "description": "Filtre par nom"},
                    {"name": "page", "in": "query", "description": "Numéro de page", "default": 1},
                    {"name": "page_size", "in": "query", "description": "Éléments par page", "default": 50},
                ],
                "responses": {
                    "200": {"description": "Liste paginée d'OU"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/ous/{{dn}}": {
            "get": {
                "summary": "Récupération d'un OU",
                "description": "Retourne les détails d'une OU identifiée par son DN.",
                "parameters": [{"name": "dn", "in": "path", "description": "DN complet de l'OU", "required": True}],
                "responses": {
                    "200": {"description": "Détails de l'OU"},
                    "404": {"description": "OU introuvable"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/portal/requests": {
            "get": {
                "summary": "Liste des demandes portail",
                "description": "Retourne les demandes de création de compte en attente de validation.",
                "parameters": [
                    {"name": "statut", "in": "query", "description": "Filtre par statut"},
                    {"name": "page", "in": "query", "description": "Numéro de page", "default": 1},
                    {"name": "page_size", "in": "query", "description": "Éléments par page", "default": 20},
                ],
                "responses": {
                    "200": {"description": "Liste des demandes portail"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/portal/requests/{{demande_id}}/approve": {
            "post": {
                "summary": "Approbation d'une demande",
                "description": "Valide une demande de création de compte : génère identifiants + mot de passe, crée le compte AD, envoie notification si activé.",
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "notify": {"type": "boolean", "description": "Envoyer les identifiants par mail"},
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "200": {"description": "Demande validée avec identifiants générés"},
                    "400": {"description": "Demande introuvable ou déjà traitée"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/portal/requests/{{demande_id}}/refuse": {
            "post": {
                "summary": "Refus d'une demande",
                "description": "Marque une demande comme refusée. Le demandeur ne reçoit rien (à répondre par un autre canal).",
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "note": {"type": "string", "description": "Note interne à joindre au journal"},
                                },
                            },
                        },
                    },
                },
                "responses": {
                    "200": {"description": "Demande refusée"},
                    "400": {"description": "Demande introuvable ou déjà traitée"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/webhooks": {
            "get": {
                "summary": "Liste des webhooks",
                "description": "Retourne la configuration des webhooks abonnés.",
                "parameters": [
                    {"name": "active", "in": "query", "description": "Ne montrer que les actifs", "default": True},
                ],
                "responses": {
                    "200": {"description": "Liste des webhooks"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/webhooks": {
            "post": {
                "summary": "Création d'un webhook",
                "description": "Enregistre un nouveau webhook pour recevoir des notifications d'événements.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "url", "events"],
                                "properties": {
                                    "name": {"type": "string", "description": "Nom lisible du webhook"},
                                    "url": {"type": "string", "format": "uri", "description": "URL cible (HTTPS recommandé)"},
                                    "events": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Événements à abonner (\"*\" = tous)",
                                    },
                                    "secret": {"type": "string", "description": "Secret HMAC (optionnel, chiffré)"},
                                    "headers": {
                                        "type": "object",
                                        "description": "Headers additionnels à envoyer",
                                        "additionalProperties": {"type": "string"},
                                    },
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "201": {"description": "Webhook créé"},
                    "400": {"description": "Données invalides"},
                    "401": {"description": "Clé API invalide"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/health": {
            "get": {
                "summary": "Health check",
                "description": "Retourne l'état de disponibilité du service API.",
                "responses": {
                    "200": {"description": "Service OK"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
        f"/api/{API_VERSION}/info": {
            "get": {
                "summary": "Informations API",
                "description": "Retourne les métadonnées de la version actuelle.",
                "responses": {
                    "200": {"description": "Informations de version"},
                },
            },
            "security": [{"ApiKeyAuth": []}],
        },
    }

    for path_pattern, path_item in manual_paths.items():
        spec.paths[path_pattern] = path_item

    return spec


def write_spec(spec: OpenAPISpec, path: Path) -> Path:
    """Sérialise le ``OpenAPISpec`` en JSON et l'écrit sur disque."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(spec.to_dict(), fh, ensure_ascii=False, indent=2, sort_keys=False)
    return path


__all__ = ["OpenAPISpec", "generate_spec", "write_spec"]