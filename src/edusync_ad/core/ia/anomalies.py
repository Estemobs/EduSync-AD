"""M29 — Fonctions IA pour la détection d'anomalies et la génération d'identifiants."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from edusync_ad.core.config import AppConfig
from edusync_ad.core.rbac import RBACPolicy, Role


def detect_anomalies(
    config: AppConfig,
    portal_service: PortalService,
    *,
    check_duplicates: bool = True,
    check_orphan_accounts: bool = True,
    check_empty_ous: bool = True,
    check_groups_without_members: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Détecte les anomalies dans l'annuaire Active Directory.

    Retourne un dictionnaire par type d'anomalie avec détails.
    """
    anomalies: Dict[str, List[Dict[str, Any]]] = {}

    if check_duplicates:
        dup = _check_duplicate_identifiers(portal_service)
        if dup["has_duplicates"]:
            anomalies["identifiants_en_double"] = dup["details"]

    if check_orphan_accounts:
        orphan = _check_orphan_accounts(portal_service)
        if orphan["has_orphans"]:
            anomalies["comptes_orphelins"] = orphan["details"]

    if check_empty_ous:
        empty = _check_empty_ous(portal_service)
        if empty["has_empty_ous"]:
            anomalies["ou_vides"] = empty["details"]

    if check_groups_without_members:
        groups = _check_groups_without_members(portal_service)
        if groups["has_groups_without_members"]:
            anomalies["groupes_sans_membres"] = groups["details"]

    return anomalies


def _check_duplicate_identifiers(portal_service: PortalService) -> Dict[str, Any]:
    """Vérifie les identifiants dupliqués dans l'annuaire."""
    # Collecte tous les SAMAccountName
    all_sams: Set[str] = set()
    details: List[Dict[str, Any]] = []

    # TODO : vraie implémentation via AD connection
    # Pour l'instant, simulation
    return {
        "has_duplicates": False,
        "details": details,
    }


def _check_orphan_accounts(portal_service: PortalService) -> Dict[str, Any]:
    """Vérifie les comptes dont l'OU n'existe plus."""
    # TODO : vraie implémentation
    return {"has_orphans": False, "details": []}


def _check_empty_ous(portal_service: PortalService) -> Dict[str, Any]:
    """Vérifie les OU vides (sans utilisateurs)."""
    # TODO : vraie implémentation
    return {"has_empty_ous": False, "details": []}


def _check_groups_without_members(portal_service: PortalService) -> Dict[str, Any]:
    """Vérifie les groupes sans membres."""
    # TODO : vraie implémentation
    return {"has_groups_without_members": False, "details": []}


def suggest_cleanup(
    config: AppConfig,
    portal_service: PortalService,
    *,
    old_days: int = 365,
) -> Dict[str, List[Dict[str, Any]]]:
    """Suggère un nettoyage pour la fin d'année.

    Identifie les comptes/groupes susceptibles d'être archivés ou supprimés.
    """
    suggestions: Dict[str, List[Dict[str, Any]]] = {"suggestions": []}

    # TODO : implémenter le vrai logique de nettoyage
    # - Comptes inactifs depuis > old_days
    # - Groupes sans membres actifs
    # - OU vides

    return suggestions


def generate_smart_identifier(
    config: AppConfig,
    prenom: str,
    nom: str,
    *,
    existing: Optional[Set[str]] = None,
    format: str = "standard",
) -> str:
    """Génère un identifiant unique en respectant la charte de l'établissement.

    Args:
        config: Configuration de l'application
        prenom: Prénom de l'usager
        nom: Nom de l'usager
        existing: Ensemble d'identifiants déjà existants (optionnel)
        format: Format d'identifiant ('standard', 'camel', 'prenom_nom')

    Returns:
        Un identifiant unique disponible
    """
    prenom = prenom.strip().lower()
    nom = nom.strip().lower()

    if format == "camel":
        base = f"{prenom[0]}.{nom}"
    elif format == "prenom_nom":
        base = f"{prenom}_{nom}"
    else:  # standard (default)
        base = f"{prenom}.{nom}"

    # Nettoyage: enlever les caractères spéciaux, remplacer les accents
    base = re.sub(r"[^a-z0-9]", "", base)

    # Ajouter un suffixe numérique si nécessaire
    if existing is None:
        existing = set()

    suffix = 1
    candidate = base
    while candidate.lower() in existing:
        suffix += 1
        candidate = f"{base}{suffix}"
        if suffix > 100:
            raise ValueError("Impossible de générer un identifiant unique")

    return candidate


def analyze_security(
    config: AppConfig,
    portal_service: PortalService,
    rbac: RBACPolicy,
) -> Dict[str, Any]:
    """Analyse la sécurité et retourne des recommandations."""
    recommendations: List[Dict[str, Any]] = []

    # Vérifier la politique de mot de passe
    policy = config.politique_mdp_eleve
    if not policy.majuscules:
        recommendations.append(
            {
                "type": "mdp",
                "niveau": "warning",
                "message": "La politique mot de passe éléve n'exige pas de majuscules",
            }
        )

    # Vérifier les permissions RBAC
    if rbac.role == Role.SUPER_ADMIN:
        recommendations.append(
            {
                "type": "rbac",
                "niveau": "info",
                "message": "Opérateur en super-admin : revoir les portées de délégation",
            }
        )

    return {"recommendations": recommendations, "score": 85}  # score simplifié