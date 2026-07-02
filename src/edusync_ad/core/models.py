from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AccountType(str, Enum):
    ELEVE = "eleve"
    PERSONNEL = "personnel"


class DoublonRule(str, Enum):
    SUFFIXE_NUMERIQUE = "suffixe_numerique"
    SUFFIXE_NUMERIQUE_SEPARATEUR = "suffixe_numerique_separateur"
    PREFIXE_NUMERIQUE = "prefixe_numerique"
    LETTRES_PRENOM = "lettres_prenom"
    LETTRES_NOM = "lettres_nom"
    ANNEE_SUFFIXE = "annee_suffixe"


class PrenomComposeRule(str, Enum):
    PREMIER_PRENOM = "premier_prenom"
    CONCATENATION = "concatenation"
    TRONCATURE = "troncature"


@dataclass
class PasswordPolicy:
    longueur: int = 10
    majuscules: bool = True
    chiffres: bool = True
    caracteres_speciaux: bool = False
    mot_de_passe_identique: bool = False
    pattern_fixe: str | None = None


@dataclass
class RawUserRow:
    """Une ligne brute telle qu'importée depuis le CSV (avant génération)."""

    prenom: str
    nom: str
    ou: str
    email_perso: str | None = None
    date_naissance: str | None = None
    numero: str | None = None


@dataclass
class GeneratedUser:
    """Une ligne de prévisualisation prête à être écrite dans l'AD."""

    source: RawUserRow
    identifiant: str
    mot_de_passe: str
    adresse_mail: str
    ou_cible: str
    groupe: str | None = None
    doublon_resolu: bool = False
    doublon_ad: bool = False
    erreur: str | None = None

    @property
    def nom_complet(self) -> str:
        return f"{self.source.prenom} {self.source.nom}"


@dataclass
class ConnectionInfo:
    domaine: str
    controleur: str
    utilisateur: str
    use_ldaps: bool = True


@dataclass
class DepartRow:
    """Une ligne du fichier de départs (désactivation ou archivage)."""

    identifiant: str
    user_dn: str | None = None
    nom_complet: str | None = None
    groupe_dns: list[str] = field(default_factory=list)
    erreur: str | None = None


@dataclass
class MigrationRow:
    """Une ligne du fichier de migration (passage de classe en fin d'année)."""

    identifiant: str
    ou_source: str
    ou_destination: str
    user_dn: str | None = None
    nom_complet: str | None = None
    erreur: str | None = None


@dataclass
class ActionLogEntry:
    timestamp: str
    action_type: str
    compte: str
    ou_source: str | None
    ou_destination: str | None
    resultat: str
    session_id: str
    simulation: bool
    detail: str = ""
    utilisateur: str = ""
