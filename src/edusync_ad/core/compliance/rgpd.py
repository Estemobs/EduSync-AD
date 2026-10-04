"""M30 — Rapport RGPD et droit à l'oubli."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from edusync_ad.core.config import AppConfig
from edusync_ad.core.portal import PortalService, PortalConfig, PortalStore, PortalUser
from edusync_ad.core.rbac import RBACPolicy, Role


def generer_rapport_rgpd(
    config: AppConfig,
    portal_service: PortalService,
    *,
    date_debut: Optional[datetime] = None,
    date_fin: Optional[datetime] = None,
    inclure_comptes: bool = True,
    inclure_journal: bool = True,
) -> Dict[str, Any]:
    """Génère un rapport RGPD complet sur les données stockées.

    Retourne un dictionnaire structuré contenant :
    - Inventaire des données personnelles
    - Durée de conservation
    - Liste des opérations d'exercice du droit à l'oubli
    - Recommandations de conformité
    """
    date_fin = date_fin or datetime.now()
    date_debut = date_debut or (date_fin - timedelta(days=365))

    rapport: Dict[str, Any] = {
        "generation_date": datetime.now().isoformat(timespec="seconds"),
        "periode": {
            "debut": date_debut.isoformat(timespec="seconds"),
            "fin": date_fin.isoformat(timespec="seconds"),
        },
        "inventaire": {},
        "droit_oubli": [],
        "recommandations": [],
    }

    if inclure_comptes:
        inventaire = _inventaire_comptes(portal_service, date_debut, date_fin)
        rapport["inventaire"] = inventaire

    if inclure_journal:
        droit_oubli = _droit_oubli_journal(portal_service, date_debut, date_fin)
        rapport["droit_oubli"] = droit_oubli

    _appliquer_recommandations(rapport)

    return rapport


def _inventaire_comptes(
    portal_service: PortalService, date_debut: datetime, date_fin: datetime
) -> Dict[str, Any]:
    """Inventaire des comptes et durée de conservation."""
    # TODO : implémenter le vrai inventaire AD + coffre MDP
    return {
        "total_comptes": 0,
        "comptes_anciens": 0,
        "age_moyen_jours": 0,
        "derniere_connexion_max": None,
        "derniere_connexion_min": None,
    }


def _droit_oubli_journal(
    portal_service: PortalService, date_debut: datetime, date_fin: datetime
) -> List[Dict[str, Any]]:
    """Retourne les entrées de journal liées au droit à l'oubli."""
    # TODO : implémenter le vrai query journal
    return []


def _appliquer_recommandations(rapport: Dict[str, Any]) -> None:
    """Ajoute des recommandations basées sur les findings."""
    recommandations = rapport.setdefault("recommandations", [])

    # Exemple : si beaucoup de comptes anciens
    if rapport["inventaire"].get("comptes_anciens", 0) > 1000:
        recommandations.append(
            {
                "type": "conservation",
                "niveau": "warning",
                "message": f"{rapport['inventaire']['comptes_anciens']} comptes anciens détectés — envisager archive RGPD",
            }
        )


def exporter_donnees_vers_csv(rapport: Dict[str, Any], path: str) -> Path:
    """Exporte le rapport RGPD au format CSV."""
    import csv
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Category", "Key", "Value"])
        for section, data in rapport.items():
            if isinstance(data, dict):
                for key, value in data.items():
                    writer.writerow([section, key, str(value)])
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        ligne = []
                        for k, v in item.items():
                            ligne.append(str(v))
                        writer.writerow([section, ", ".join(item.keys()), ", ".join(ligne)])

    return path


__all__ = ["generer_rapport_rgpd", "exporter_donnees_vers_csv"]