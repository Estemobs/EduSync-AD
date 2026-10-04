"""M32 — Sauvegarde / Restauration / Migration appli.

Ce module fournit les fonctionnalités de sauvegarde, restauration et migration :
- Backup complet (config, journal, coffre MDP, modèles, certificats) → ZIP chiffré
- Restore 1-clic sur nouvelle machine
- Migration données vN → vN+1 auto

Architecture : stdlib (zipfile, AES-256 via core/crypto).
"""

from __future__ import annotations

from typing import Any

from edusync_ad.core.sauvegarde.backup import generer_backup_complet, verifier_integrite_backup
from edusync_ad.core.sauvegarde.migration import migrer_donnees_vn_vn1
from edusync_ad.core.sauvegarde.restore import restorer_dans_nouvelle_machine

__all__ = [
    "generer_backup_complet",
    "verifier_integrite_backup",
    "restorer_dans_nouvelle_machine",
    "migrer_donnees_vn_vn1",
]