from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

from edusync_ad.core.models import DoublonRule, PasswordPolicy, PrenomComposeRule

APP_NAME = "EduSyncAD"
APP_AUTHOR = "EduSyncAD"


def config_dir() -> Path:
    path = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class AppConfig:
    """Paramètres globaux (§12) couverts par ce lot — utilisés par le Module 1."""

    # Nomenclature des identifiants
    identifiant_format_eleve: str = "prenom.nom"
    identifiant_format_personnel: str = "p.nom"

    # Règle de résolution des doublons
    regle_doublons: DoublonRule = DoublonRule.SUFFIXE_NUMERIQUE

    # Politiques de mot de passe (distinctes élèves / personnels)
    politique_mdp_eleve: PasswordPolicy = field(
        default_factory=lambda: PasswordPolicy(
            longueur=8, majuscules=False, chiffres=True, caracteres_speciaux=False
        )
    )
    politique_mdp_personnel: PasswordPolicy = field(
        default_factory=lambda: PasswordPolicy(
            longueur=12, majuscules=True, chiffres=True, caracteres_speciaux=True
        )
    )

    # Adresses mail
    domaine_mail: str = "exemple.fr"
    format_mail: str = "{P}.{N}"

    # Prénoms composés
    regle_prenom_compose: PrenomComposeRule = PrenomComposeRule.PREMIER_PRENOM

    # Groupes de classe automatiques
    groupes_classe_auto: bool = True

    # Apparence
    theme: str = "clair"  # "clair" | "sombre"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["regle_doublons"] = self.regle_doublons.value
        data["regle_prenom_compose"] = self.regle_prenom_compose.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        kwargs = dict(data)
        if "regle_doublons" in kwargs:
            kwargs["regle_doublons"] = DoublonRule(kwargs["regle_doublons"])
        if "regle_prenom_compose" in kwargs:
            kwargs["regle_prenom_compose"] = PrenomComposeRule(kwargs["regle_prenom_compose"])
        if "politique_mdp_eleve" in kwargs and isinstance(kwargs["politique_mdp_eleve"], dict):
            kwargs["politique_mdp_eleve"] = PasswordPolicy(**kwargs["politique_mdp_eleve"])
        if "politique_mdp_personnel" in kwargs and isinstance(
            kwargs["politique_mdp_personnel"], dict
        ):
            kwargs["politique_mdp_personnel"] = PasswordPolicy(**kwargs["politique_mdp_personnel"])
        known_fields = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in kwargs.items() if k in known_fields}
        return cls(**kwargs)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_file_path()
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppConfig()
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: Path | None = None) -> None:
    path = path or config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
