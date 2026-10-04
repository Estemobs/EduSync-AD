"""M31 — DFS / IIS / WebDAV / FTP isolé.

Ce module gère les services de fichiers et de partage :
- DFS (Distributed File System) — espaces partagés avec réplication multi-serveurs
- IIS (Internet Information Services) — publication WebDAV auto pour dossiers
- WebDAV — accès en lecture/écriture sur les dossiers partagés
- FTP isolé AD — authentification AD, priorité basse, héritage de droits

Architecture : stdlib uniquement (win32com pour IIS/DFS sous Windows, pas de dépendances externes).
Sous Linux/Mac : mode observation uniquement, pas de configuration active.
"""

from __future__ import annotations

from typing import Any

from edusync_ad.core.compliance import generer_config_webdav, verifier_ftp_isole, recommandations_partage
from edusync_ad.core.config import AppConfig
from edusync_ad.core.rbac import RBACPolicy, Role

__all__ = [
    "analyser_topologie_dfs",
    "generer_config_webdav",
    "verifier_ftp_isole",
    "recommandations_partage",
]