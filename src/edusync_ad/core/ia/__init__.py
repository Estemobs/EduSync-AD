"""M29 — Intelligence artificielle / Assistant.

Ce module fournit des fonctionnalités d'assistant IA pour EduSync AD :
- Détection d'anomalies (doublons, comptes orphelins, OU vides, groupes sans membres)
- Suggestion de nettoyage fin d'année
- Génération d'identifiants "intelligente"
- Analyse de logs avec recommandations de sécurité
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from edusync_ad.core.config import AppConfig
from edusync_ad.core.rbac import RBACPolicy, Role

from .anomalies import detect_anomalies, suggest_cleanup, generate_smart_identifier, analyze_security

__all__ = [
    "detect_anomalies",
    "suggest_cleanup",
    "generate_smart_identifier",
    "analyze_security",
]