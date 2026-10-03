"""Profils utilisateurs AD (M13) — Itinérant (roaming), local, obligatoire (.man).

Configuration par OU ou groupe : chemin profil itinérant, local, obligatoire.
Application en masse lors création/migration.
Support variables %USERNAME%, %SAM%, %OU%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError


class ProfileType(str, Enum):
    """Type de profil utilisateur."""
    LOCAL = "local"                    # Profil local uniquement (défaut AD)
    ROAMING = "roaming"                # Profil itinérant (roaming)
    MANDATORY = "mandatory"            # Profil obligatoire (.man - lecture seule)


@dataclass
class ProfileConfig:
    """Configuration d'un profil utilisateur."""
    profile_type: ProfileType = ProfileType.LOCAL
    roaming_path: str = ""             # Ex: \\\\srv\\profil\\%USERNAME%
    local_path: str = ""               # Ex: C:\\Users\\%USERNAME% (rarement utilisé)
    mandatory_path: str = ""           # Ex: \\\\srv\\profil_mandatory\\eleve.man
    
    # Variables supportées: %USERNAME%, %SAM%, %OU%, %DOMAIN%
    
    def resolve_path(self, template: str, username: str, sam: str, 
                     ou_dn: str, domain: str) -> str:
        """Résout les variables dans un chemin de profil."""
        if not template:
            return ""
        
        # Extraire le nom de l'OU (dernier composant OU=...)
        ou_name = ""
        if ou_dn:
            for part in ou_dn.split(","):
                if part.strip().upper().startswith("OU="):
                    ou_name = part.split("=", 1)[1]
                    break
        
        variables = {
            "USERNAME": username,
            "SAM": sam,
            "OU": ou_name,
            "DOMAIN": domain,
        }
        
        result = template
        for var, value in variables.items():
            result = result.replace(f"%{var}%", value)
        
        return result
    
    def get_profile_path(self, username: str, sam: str, ou_dn: str, 
                         domain: str) -> str:
        """Retourne le chemin résolu selon le type de profil."""
        if self.profile_type == ProfileType.ROAMING:
            return self.resolve_path(self.roaming_path, username, sam, ou_dn, domain)
        elif self.profile_type == ProfileType.MANDATORY:
            return self.resolve_path(self.mandatory_path, username, sam, ou_dn, domain)
        elif self.profile_type == ProfileType.LOCAL:
            return self.resolve_path(self.local_path, username, sam, ou_dn, domain)
        return ""


@dataclass
class ProfileTemplate:
    """Modèle de profil préétabli."""
    name: str
    description: str
    config: ProfileConfig
    
    # Modèles intégrés
    @classmethod
    def builtin_templates(cls) -> list["ProfileTemplate"]:
        return [
            ProfileTemplate(
                name="Élève - Profil itinérant standard",
                description="Profil itinérant sur serveur de profils partagé",
                config=ProfileConfig(
                    profile_type=ProfileType.ROAMING,
                    roaming_path=r"\\srv-profil\profils\%USERNAME%",
                ),
            ),
            ProfileTemplate(
                name="Élève - Profil obligatoire (kiosque)",
                description="Profil verrouillé .man pour salles informatiques",
                config=ProfileConfig(
                    profile_type=ProfileType.MANDATORY,
                    mandatory_path=r"\\srv-profil\mandatory\eleve.man",
                ),
            ),
            ProfileTemplate(
                name="Personnel - Profil itinérant",
                description="Profil itinérant pour enseignants/administratifs",
                config=ProfileConfig(
                    profile_type=ProfileType.ROAMING,
                    roaming_path=r"\\srv-profil\profils_personnel\%USERNAME%",
                ),
            ),
            ProfileTemplate(
                name="Local uniquement",
                description="Pas de profil itinérant (par défaut AD)",
                config=ProfileConfig(profile_type=ProfileType.LOCAL),
            ),
        ]


class ProfileManager:
    """Gestionnaire de profils utilisateurs AD.
    
    Applique la configuration de profil (profilePath, homeDirectory, 
    homeDrive, scriptPath) lors de la création ou migration de comptes.
    """
    
    def __init__(self, ad_connection: ADConnection):
        self._ad = ad_connection
        self._ou_configs: dict[str, ProfileConfig] = {}      # OU DN -> config
        self._group_configs: dict[str, ProfileConfig] = {}   # Group DN -> config
        self._default_config = ProfileConfig()
    
    def set_ou_config(self, ou_dn: str, config: ProfileConfig) -> None:
        """Définit la config de profil pour une OU."""
        self._ou_configs[ou_dn] = config
    
    def set_group_config(self, group_dn: str, config: ProfileConfig) -> None:
        """Définit la config de profil pour un groupe."""
        self._group_configs[group_dn] = config
    
    def set_default_config(self, config: ProfileConfig) -> None:
        """Définit la config par défaut."""
        self._default_config = config
    
    def get_config_for_user(self, user_dn: str, username: str, sam: str, 
                            ou_dn: str, domain: str, 
                            user_groups: list[str] | None = None) -> ProfileConfig:
        """Détermine la config applicable pour un utilisateur.
        
        Priorité: Groupe > OU > Défaut
        """
        # 1. Vérifier les groupes de l'utilisateur
        if user_groups:
            for group_dn in user_groups:
                if group_dn in self._group_configs:
                    return self._group_configs[group_dn]
        
        # 2. Vérifier l'OU et ses parentes
        current_ou = ou_dn
        while current_ou:
            if current_ou in self._ou_configs:
                return self._ou_configs[current_ou]
            # Remonter à l'OU parente
            parts = current_ou.split(",", 1)
            current_ou = parts[1] if len(parts) > 1 else ""
        
        # 3. Config par défaut
        return self._default_config
    
    def apply_profile(self, user_dn: str, username: str, sam: str, 
                      ou_dn: str, domain: str, 
                      user_groups: list[str] | None = None) -> dict[str, str]:
        """Applique la config de profil à un utilisateur AD.
        
        Retourne les attributs modifiés pour audit.
        """
        config = self.get_config_for_user(user_dn, username, sam, ou_dn, domain, user_groups)
        changes = {}
        
        profile_path = config.get_profile_path(username, sam, ou_dn, domain)
        
        if config.profile_type == ProfileType.ROAMING and profile_path:
            # Profil itinérant : profilePath
            changes["profilePath"] = profile_path
            # homeDirectory souvent sur le même partage
            home_dir = profile_path.replace("profil", "homes").replace("profils", "homes")
            if home_dir == profile_path:
                home_dir = rf"\\{domain.split('.')[0]}\homes\{sam}"
            changes["homeDirectory"] = home_dir
            changes["homeDrive"] = "H:"
            
        elif config.profile_type == ProfileType.MANDATORY and profile_path:
            # Profil obligatoire : profilePath pointe vers .man
            changes["profilePath"] = profile_path
            # Pas de homeDirectory pour profil obligatoire généralement
            
        elif config.profile_type == ProfileType.LOCAL:
            # Profil local : pas de profilePath (AD utilise le local)
            # Mais on peut quand même définir homeDirectory
            if config.local_path:
                changes["homeDirectory"] = config.resolve_path(
                    config.local_path, username, sam, ou_dn, domain)
                changes["homeDrive"] = "H:"
        
        # Appliquer les changements
        for attr, value in changes.items():
            try:
                self._ad.update_user_attribute(user_dn, attr, value)
            except ADError as exc:
                # Logger mais continuer
                print(f"Warning: Failed to set {attr}: {exc}")
        
        return changes


# Configuration persistante (JSON)
import json
from edusync_ad.core.config import config_dir

PROFILE_CONFIG_FILE = config_dir() / "profile_configs.json"


def load_profile_configs() -> dict:
    """Charge les configs de profil depuis le fichier JSON."""
    if PROFILE_CONFIG_FILE.exists():
        try:
            with PROFILE_CONFIG_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_profile_configs(configs: dict) -> None:
    """Sauvegarde les configs de profil dans le fichier JSON."""
    PROFILE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROFILE_CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2, ensure_ascii=False)


def profile_config_to_dict(config: ProfileConfig) -> dict:
    return {
        "profile_type": config.profile_type.value,
        "roaming_path": config.roaming_path,
        "local_path": config.local_path,
        "mandatory_path": config.mandatory_path,
    }


def dict_to_profile_config(data: dict) -> ProfileConfig:
    return ProfileConfig(
        profile_type=ProfileType(data.get("profile_type", "local")),
        roaming_path=data.get("roaming_path", ""),
        local_path=data.get("local_path", ""),
        mandatory_path=data.get("mandatory_path", ""),
    )


def load_all_profile_configs() -> tuple[dict[str, ProfileConfig], dict[str, ProfileConfig], ProfileConfig]:
    """Charge toutes les configs : OU, groupes, défaut."""
    data = load_profile_configs()
    ou_configs = {k: dict_to_profile_config(v) for k, v in data.get("ou_configs", {}).items()}
    group_configs = {k: dict_to_profile_config(v) for k, v in data.get("group_configs", {}).items()}
    default_config = dict_to_profile_config(data.get("default_config", {}))
    return ou_configs, group_configs, default_config


def save_all_profile_configs(
    ou_configs: dict[str, ProfileConfig],
    group_configs: dict[str, ProfileConfig],
    default_config: ProfileConfig
) -> None:
    """Sauvegarde toutes les configs."""
    data = {
        "ou_configs": {k: profile_config_to_dict(v) for k, v in ou_configs.items()},
        "group_configs": {k: profile_config_to_dict(v) for k, v in group_configs.items()},
        "default_config": profile_config_to_dict(default_config),
    }
    save_profile_configs(data)