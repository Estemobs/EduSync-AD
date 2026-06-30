"""Génération des mots de passe (§4, §12 du cahier des charges).

Politique configurable séparément pour élèves et personnels : longueur,
inclusion optionnelle de majuscules / chiffres / caractères spéciaux, mot de
passe identique pour tout un import, ou pattern fixe combinable avec les
variables d'identifiant (ex. "Ecole{AN}!").
"""

from __future__ import annotations

import random
import re
import string
from datetime import date

from edusync_ad.core.identifiers import clean_token
from edusync_ad.core.models import PasswordPolicy

LOWERCASE = string.ascii_lowercase
UPPERCASE = string.ascii_uppercase
DIGITS = string.digits
SPECIALS = "!@#$%^&*-_+=?"

MIN_LENGTH = 6
MAX_LENGTH = 32

_VAR_RE = re.compile(r"\{(P|N|AN|ANNEE|p\d+|n\d+)\}")
_sysrand = random.SystemRandom()


def render_password_pattern(
    pattern: str, prenom_raw: str, nom_raw: str, year: int | None = None
) -> str:
    """Substitue les variables d'identifiant dans un pattern de mot de passe fixe.

    Contrairement au moteur d'identifiants, le pattern conserve sa casse et
    ses caractères spéciaux littéraux (ex. "Ecole{AN}!" -> "Ecole25!") ;
    seules les variables sont remplacées.
    """
    prenom = clean_token(prenom_raw)
    nom = clean_token(nom_raw)
    year = year if year is not None else date.today().year

    def repl(match: re.Match) -> str:
        token = match.group(1)
        if token == "P":
            return prenom
        if token == "N":
            return nom
        if token == "ANNEE":
            return str(year)
        if token == "AN":
            return str(year)[-2:]
        if token.startswith("p"):
            return prenom[: int(token[1:])]
        if token.startswith("n"):
            return nom[: int(token[1:])]
        return match.group(0)

    return _VAR_RE.sub(repl, pattern)


def generate_random_password(policy: PasswordPolicy) -> str:
    length = max(MIN_LENGTH, min(MAX_LENGTH, policy.longueur))

    categories = [LOWERCASE]
    if policy.majuscules:
        categories.append(UPPERCASE)
    if policy.chiffres:
        categories.append(DIGITS)
    if policy.caracteres_speciaux:
        categories.append(SPECIALS)

    required = [_sysrand.choice(cat) for cat in categories]
    pool = "".join(categories)
    chars = required[:length]
    while len(chars) < length:
        chars.append(_sysrand.choice(pool))
    _sysrand.shuffle(chars)
    return "".join(chars)


def generate_password(
    policy: PasswordPolicy,
    *,
    prenom: str = "",
    nom: str = "",
    year: int | None = None,
) -> str:
    if policy.pattern_fixe:
        return render_password_pattern(policy.pattern_fixe, prenom, nom, year)
    return generate_random_password(policy)


def generate_passwords_for_batch(
    policy: PasswordPolicy,
    rows: list[tuple[str, str]],
    *,
    year: int | None = None,
) -> list[str]:
    """Génère un mot de passe par ligne (prenom, nom), en respectant l'option
    "mot de passe identique pour tout le lot"."""
    if policy.mot_de_passe_identique:
        shared = generate_password(policy, year=year)
        return [shared for _ in rows]
    return [
        generate_password(policy, prenom=prenom, nom=nom, year=year) for prenom, nom in rows
    ]
