"""Moteur de génération d'identifiants (§4 du cahier des charges).

Un seul moteur de template gère les formats prédéfinis (table p.5-6) et le
format personnalisé libre. Les formats prédéfinis sont eux-mêmes des templates
(ex. "prenom.nom" -> "{P}.{N}"), sauf "prenomNom" et "NomPrenom" qui utilisent
de la casse (camelCase) non exprimable avec les variables documentées et sont
donc traités à part.

Note d'implémentation : la table « Variables » du cahier des charges définit
{p2} comme « 2 premières lettres du prénom » (préfixe), ce qui est cohérent
avec la table des formats prédéfinis (ex. "pp.nom" -> "th.martin" = {p2}.{N}).
La sémantique de préfixe est donc celle retenue ici ; quelques lignes de la
table « Exemples de formats personnalisés » (ex. {p1}{p2}.{N} -> th.martin)
ne sont pas reproductibles avec cette définition et semblent contenir une
coquille dans le document source — elles ne sont pas prises comme référence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Iterator

from edusync_ad.core.models import DoublonRule, PrenomComposeRule

_VAR_RE = re.compile(r"\{(P|N|AN|ANNEE|p\d+|n\d+)\}")
_INVALID_CHARS_RE = re.compile(r"[^a-z0-9._-]")

PRESETS: dict[str, str] = {
    "prenom": "{P}",
    "nom": "{N}",
    "prenomnom": "{P}{N}",
    "nomprenom": "{N}{P}",
    "prenom.nom": "{P}.{N}",
    "nom.prenom": "{N}.{P}",
    "p.nom": "{p1}.{N}",
    "nom.p": "{N}.{p1}",
    "pnom": "{p1}{N}",
    "nomp": "{N}{p1}",
    "pp.nom": "{p2}.{N}",
    "ppp.nom": "{p3}.{N}",
    "nom.pp": "{N}.{p2}",
    "nom.ppp": "{N}.{p3}",
    "prenom.nnn": "{P}.{n3}",
    "prenom_nom": "{P}_{N}",
    "nom_prenom": "{N}_{P}",
}

CAMEL_PRESETS = ("prenomNom", "NomPrenom")


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def clean_token(text: str) -> str:
    """Nettoie un prénom/nom : accents, apostrophes, espaces, caractères interdits."""
    text = strip_accents(text).lower()
    text = text.replace("'", "").replace(" ", "")
    return _INVALID_CHARS_RE.sub("", text)


def apply_prenom_compose_rule(prenom_raw: str, rule: PrenomComposeRule) -> str:
    """Applique la règle de prénom composé sur le prénom brut (avant nettoyage)."""
    parts = [p for p in re.split(r"[-\s]+", prenom_raw.strip()) if p]
    if not parts:
        return prenom_raw
    if rule == PrenomComposeRule.CONCATENATION:
        return "".join(parts)
    # PREMIER_PRENOM et TRONCATURE aboutissent tous deux à ne garder que ce qui
    # précède le premier séparateur (tiret ou espace).
    return parts[0]


def _camel_identifier(format_key: str, prenom: str, nom: str) -> str:
    if format_key == "prenomNom":
        return f"{prenom}{nom[:1].upper()}{nom[1:]}"
    if format_key == "NomPrenom":
        return f"{nom[:1].upper()}{nom[1:]}{prenom[:1].upper()}{prenom[1:]}"
    raise ValueError(f"Preset camelCase inconnu : {format_key}")


def render_template(
    template: str,
    prenom: str,
    nom: str,
    *,
    year: int | None = None,
    override: tuple[str, int] | None = None,
) -> str:
    """Rend un template avec les variables {P} {N} {p1..} {n1..} {ANNEE} {AN}.

    `override` force la longueur du préfixe pour toutes les variables {pN}
    (si override[0] == "p") ou {nN} (si override[0] == "n") — utilisé par la
    règle de doublon « lettres supplémentaires ».
    """
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
            length = override[1] if override and override[0] == "p" else int(token[1:])
            return prenom[:length]
        if token.startswith("n"):
            length = override[1] if override and override[0] == "n" else int(token[1:])
            return nom[:length]
        return match.group(0)

    return clean_token(_VAR_RE.sub(repl, template))


@dataclass
class IdentifierEngine:
    format_key: str
    doublon_rule: DoublonRule = DoublonRule.SUFFIXE_NUMERIQUE
    prenom_compose_rule: PrenomComposeRule = PrenomComposeRule.PREMIER_PRENOM
    separateur_doublon: str = "-"
    annee: int | None = None

    def _resolved_template(self) -> str | None:
        """Le template effectif, ou None pour les presets camelCase."""
        if self.format_key in CAMEL_PRESETS:
            return None
        return PRESETS.get(self.format_key, self.format_key)

    def _clean_parts(self, prenom_raw: str, nom_raw: str) -> tuple[str, str]:
        prenom = clean_token(apply_prenom_compose_rule(prenom_raw, self.prenom_compose_rule))
        nom = clean_token(nom_raw)
        return prenom, nom

    def base_identifier(self, prenom_raw: str, nom_raw: str) -> str:
        prenom, nom = self._clean_parts(prenom_raw, nom_raw)
        if self.format_key in CAMEL_PRESETS:
            return _camel_identifier(self.format_key, prenom, nom)
        template = self._resolved_template()
        return render_template(template, prenom, nom, year=self.annee)

    def generate_unique(
        self, prenom_raw: str, nom_raw: str, existing_ids: set[str]
    ) -> tuple[str, bool]:
        """Retourne (identifiant, doublon_resolu)."""
        existing_lower = {i.lower() for i in existing_ids}
        base = self.base_identifier(prenom_raw, nom_raw)
        if base.lower() not in existing_lower:
            return base, False

        prenom, nom = self._clean_parts(prenom_raw, nom_raw)
        for candidate in self._doublon_candidates(base, prenom, nom):
            if candidate.lower() not in existing_lower:
                return candidate, True
        raise ValueError(
            f"Impossible de générer un identifiant unique pour {prenom_raw} {nom_raw}"
        )

    def _doublon_candidates(self, base: str, prenom: str, nom: str) -> Iterator[str]:
        rule = self.doublon_rule
        if rule == DoublonRule.SUFFIXE_NUMERIQUE:
            yield from self._numeric_suffix_candidates(base)
        elif rule == DoublonRule.SUFFIXE_NUMERIQUE_SEPARATEUR:
            for n in range(2, 1000):
                yield f"{base}{self.separateur_doublon}{n}"
        elif rule == DoublonRule.PREFIXE_NUMERIQUE:
            for n in range(2, 1000):
                yield f"{n}.{base}"
        elif rule == DoublonRule.LETTRES_PRENOM:
            yield from self._lettres_candidates("p", base, prenom, nom)
            yield from self._numeric_suffix_candidates(base)
        elif rule == DoublonRule.LETTRES_NOM:
            yield from self._lettres_candidates("n", base, prenom, nom)
            yield from self._numeric_suffix_candidates(base)
        elif rule == DoublonRule.ANNEE_SUFFIXE:
            year = self.annee if self.annee is not None else date.today().year
            yield f"{base}{year}"
            yield from self._numeric_suffix_candidates(base)

    def _numeric_suffix_candidates(self, base: str) -> Iterator[str]:
        for n in range(2, 1000):
            yield f"{base}{n}"

    def _lettres_candidates(
        self, which: str, base: str, prenom: str, nom: str
    ) -> Iterator[str]:
        template = self._resolved_template()
        match = re.search(rf"\{{{which}(\d+)\}}", template) if template else None
        if not match:
            return
        start_length = int(match.group(1)) + 1
        max_length = len(prenom) if which == "p" else len(nom)
        for length in range(start_length, max_length + 1):
            candidate = render_template(
                template, prenom, nom, year=self.annee, override=(which, length)
            )
            if candidate != base:
                yield candidate
