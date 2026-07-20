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
    """Paramètres globaux (§12)."""

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

    # Adresses mail — domaine_mail vide = utiliser automatiquement le domaine AD connecté
    domaine_mail: str = ""
    format_mail: str = "{P}.{N}"

    # Prénoms composés
    regle_prenom_compose: PrenomComposeRule = PrenomComposeRule.PREMIER_PRENOM

    # Groupes de classe automatiques
    groupes_classe_auto: bool = True

    # OU parente sous laquelle les OU de classe (colonne "classe" d'un import)
    # sont recherchées/créées, ex. "OU=eleves,DC=lycee,DC=local". Si vide, la
    # racine du domaine connecté est utilisée automatiquement — la colonne
    # "classe" seule doit toujours suffire à créer/remplir la bonne OU, sans
    # configuration préalable obligatoire.
    ou_parente_classes: str = ""

    # Gestion des départs
    ou_archive: str = ""
    delai_suppression_jours: int = 30

    # Apparence
    theme: str = "clair"  # "clair" | "sombre"
    langue: str = "fr"    # "fr" | "en"

    # Sécurité de la connexion LDAPS — voir core/ad/connection.py.
    # Un AD interne utilise presque toujours un certificat émis par sa propre
    # autorité (AD CS), absente du magasin de confiance du poste : indiquer
    # son certificat racine ici évite d'avoir à désactiver la vérification.
    ldaps_verifier_certificat: bool = True
    ldaps_chemin_certificat_ca: str = ""

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
