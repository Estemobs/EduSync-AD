"""M31 — Topologie DFS (Distributed File System)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from edusync_ad.core.config import AppConfig
from edusync_ad.core.rbac import RBACPolicy, Role


def analyser_topologie_dfs(config: AppConfig) -> Dict[str, Any]:
    """Analyse la topologie DFS actuelle de l'établissement.

    Retourne :
    - serveurs DFS actifs
    - espaces partagés avec leurs cibles
    - état de réplication
    - recommandations d'optimisation
    """
    # TODO : implémenter la lecture réelle de la topologie DFS
    # Sous Windows : via wmi/DsRoleGetPrimaryDomainInformationError
    # Sous Linux/Mac : mode observation, retour vide structuré

    return {
        "serveurs_dfs": [],
        "espaces_partages": [],
        "replication_etat": "unknown",
        "recommandations": [
            "Topologie DFS non détectée — vérifier la présence du rôle DFS sur les serveurs",
            "Sous Linux/Mac : cette analyse s'effectue sur des serveurs Windows uniquement",
        ],
    }


def generer_config_webdav(
    config: AppConfig,
    *,
    racine_dossier: str,
    protocole: str = "http",
) -> Dict[str, Any]:
    """Génère une configuration IIS WebDAV pour la publication de dossiers.

    Args:
        config: Configuration applicative
        racine_dossier: Chemin racine des dossiers à publier
        protocole: 'http' ou 'https'

    Returns:
        Configuration IIS compatible pour publication WebDAV
    """
    # TODO : implémenter la génération réelle de configuration IIS
    # Sous Windows : utiliser appcmd ou modification des métadirives IIS
    # Sous Linux/Mac : mode observation

    return {
        "serveur_iis": None,
        "point_de_vitrage": f"{protocole}://{config.hote or 'localhost'}/webdav",
        "racine_dossier": racine_dossier,
        "protocole": protocole,
        "authentification": "Windows Integrated Authentication (NTLM/Kerberos)",
        "statut": "configuration_générée",
        "note": "Configuration à appliquer manuellement sur le serveur IIS",
    }


def verifier_ftp_isole(
    config: AppConfig,
    *,
    host: Optional[str] = None,
    port: int = 21,
) -> Dict[str, Any]:
    """Vérifie la configuration d'un serveur FTP isolé AD.

    Retourne l'état de sécurité du serveur FTP et les recommandations.

    Notes :
    - Le FTP isolé AD utilise l'authentification AD (pas de base d'utilisateurs locale)
    - Les droits sont hérités des groupes et des ACL NTFS
    - La priorité est basse par rapport au LDAP/LDAPS
    """
    # TODO : implémenter le vrai vérification
    # Sous Windows : vérifier le service FTP, les métadirives IIS, les ACL
    # Sous Linux/Mac : mode observation

    return {
        "ftp_actif": False,
        "authentification": "AD isolée (pas d'utilisateurs locaux)",
        "droits": "Héritage des groupes AD + ACL NTFS",
        "priorite": " basse (après LDAPS, avant services classiques)",
        "recommandations": [
            "FTP non détecté ou non configuré",
            "Si nécessaire : configurer un serveur FTP avec authentification AD",
            "Privilégier SFTP ou HTTPS/WebDAV pour la sécurité",
        ],
    }


def recommandations_partage(
    config: AppConfig,
    mode: str = "dfs",
) -> Dict[str, Any]:
    """Recommandations pour le partage de fichiers selon le mode choisi.

    Args:
        config: Configuration applicative
        mode: 'dfs', 'iis', 'ftp', 'webdav'

    Returns:
        Recommandations adaptées au mode de partage sélectionné
    """
    recommandations: Dict[str, Any] = {
        "mode": mode,
        "recommandations": [],
        "statut": "analyse_effectuée",
    }

    if mode == "dfs":
        recommandations.extend([
            "Utiliser DFS-R pour la réplication multi-sites",
            "Configurer les noms de cible DFS pour la résilience",
            "Surveiller l'état des liens DFS régulièrement",
            "Placer les serveurs DFS sur des sous-réseaux différents pour la redondance",
        ])
    elif mode == "iis":
        recommandations.extend([
            "Activer le rôle serveur IIS avec les services Web",
            "Configurer le service WebDAV dans IIS Manager",
            "Utiliser l'authentification Windows intégrée",
            "Appliquer les ACL NTFS sur les dossiers partagés",
            "Configurer les limites de taille de fichier et de stockage",
        ])
    elif mode == "ftp":
        recommandations.extend([
            "Privilégier SFTP ou HTTPS/WebDAV pour la sécurité",
            "Si FTP nécessaire : configurer authentification AD isolée",
            "Chiffrer les transferts avec TLS/SSL",
            "Surveiller les tentatives de connexion échouées",
        ])
    elif mode == "webdav":
        recommandations.extend([
            "Utiliser WebDAV over HTTPS pour le chiffrement",
            "Configurer des en-têtes de sécurité CORS",
            "Appliquer les stratégies de verrouillage de fichiers",
            "Utiliser l'authentification intégrée Windows",
            "Limiter les méthodes HTTP autorisées (PUT, DELETE, MKCOL)",
        ])

    return recommandations