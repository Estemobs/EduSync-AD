"""M30 — Conformité & Sécurité avancée.

Ce module fournit les fonctionnalités de conformité RGPD et de sécurité avancée :
- Rapport RGPD : données personnelles stockées, droit à l'oubli
- MFA admin (Multi-Factor Authentication) pour la connexion admin
- Session unique (Single Session) — verrouillage app par MDP
- Granularité MDP (Fine-Grained Password Policies / PSO)
- Signature numérique exports (horodatage, non-répudiation)
"""

from __future__ import annotations

from typing import Any

from edusync_ad.core.compliance.rgpd import generer_rapport_rgpd, exporter_donnees_vers_csv
from edusync_ad.core.compliance.mfa import verifier_mfa_admin, generer_cle_seche, verifier_session_unique, analyser_granularite_mdp, generer_signature_numerique

__all__ = [
    "generer_rapport_rgpd",
    "exporter_donnees_vers_csv",
    "verifier_mfa_admin",
    "generer_cle_seche",
    "verifier_session_unique",
    "analyser_granularite_mdp",
    "generer_signature_numerique",
]