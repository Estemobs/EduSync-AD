import pytest

from edusync_ad.core.identifiers import IdentifierEngine, clean_token, strip_accents
from edusync_ad.core.models import DoublonRule, PrenomComposeRule

# Table "Génération des identifiants — Formats prédéfinis" (Thomas Martin), p.5-6 du
# cahier des charges.
PREDEFINED_EXAMPLES = [
    ("prenom", "thomas"),
    ("nom", "martin"),
    ("prenomnom", "thomasmartin"),
    ("nomprenom", "martinthomas"),
    ("prenom.nom", "thomas.martin"),
    ("nom.prenom", "martin.thomas"),
    ("p.nom", "t.martin"),
    ("nom.p", "martin.t"),
    ("pnom", "tmartin"),
    ("nomp", "martint"),
    ("pp.nom", "th.martin"),
    ("ppp.nom", "tho.martin"),
    ("nom.pp", "martin.th"),
    ("nom.ppp", "martin.tho"),
    ("prenom.nnn", "thomas.mar"),
    ("prenom_nom", "thomas_martin"),
    ("nom_prenom", "martin_thomas"),
    ("prenomNom", "thomasMartin"),
    ("NomPrenom", "MartinThomas"),
]


@pytest.mark.parametrize("format_key,expected", PREDEFINED_EXAMPLES)
def test_predefined_formats(format_key, expected):
    engine = IdentifierEngine(format_key=format_key)
    assert engine.base_identifier("Thomas", "Martin") == expected


def test_strip_accents():
    assert strip_accents("Éléonore") == "Eleonore"
    assert strip_accents("François") == "Francois"


def test_clean_token_removes_apostrophes_spaces_and_invalid_chars():
    assert clean_token("O'Brien") == "obrien"
    assert clean_token("Jean Paul") == "jeanpaul"
    assert clean_token("Renée") == "renee"
    assert clean_token("Müller!") == "muller"


def test_custom_template_single_variable_examples():
    # Exemples internes au document qui restent cohérents avec la définition
    # de préfixe documentée pour {pN}/{nN} (cf. note dans identifiers.py).
    engine = IdentifierEngine(format_key="eleve.{p1}.{N}")
    assert engine.base_identifier("Thomas", "Martin") == "eleve.t.martin"

    engine = IdentifierEngine(format_key="{p1}{N}{AN}", annee=2025)
    assert engine.base_identifier("Thomas", "Martin") == "tmartin25"

    engine = IdentifierEngine(format_key="{P}.{n3}")
    assert engine.base_identifier("Thomas", "Martin") == "thomas.mar"


def test_prenom_compose_premier_prenom():
    engine = IdentifierEngine(
        format_key="prenom.nom", prenom_compose_rule=PrenomComposeRule.PREMIER_PRENOM
    )
    assert engine.base_identifier("Jean-Pierre", "Dupont") == "jean.dupont"


def test_prenom_compose_concatenation():
    engine = IdentifierEngine(
        format_key="prenom.nom", prenom_compose_rule=PrenomComposeRule.CONCATENATION
    )
    assert engine.base_identifier("Jean-Pierre", "Dupont") == "jeanpierre.dupont"


def test_doublon_suffixe_numerique_default():
    engine = IdentifierEngine(format_key="prenom.nom")
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"thomas.martin", "thomas.martin2"}
    )
    assert identifiant == "thomas.martin3"
    assert doublon is True


def test_doublon_suffixe_numerique_separateur():
    engine = IdentifierEngine(
        format_key="prenom.nom",
        doublon_rule=DoublonRule.SUFFIXE_NUMERIQUE_SEPARATEUR,
        separateur_doublon="-",
    )
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"thomas.martin"}
    )
    assert identifiant == "thomas.martin-2"
    assert doublon is True


def test_doublon_prefixe_numerique():
    engine = IdentifierEngine(format_key="prenom.nom", doublon_rule=DoublonRule.PREFIXE_NUMERIQUE)
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"thomas.martin"}
    )
    assert identifiant == "2.thomas.martin"
    assert doublon is True


def test_doublon_lettres_prenom():
    engine = IdentifierEngine(format_key="p.nom", doublon_rule=DoublonRule.LETTRES_PRENOM)
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"t.martin", "th.martin"}
    )
    assert identifiant == "tho.martin"
    assert doublon is True


def test_doublon_lettres_nom():
    engine = IdentifierEngine(format_key="prenom.nnn", doublon_rule=DoublonRule.LETTRES_NOM)
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"thomas.mar"}
    )
    assert identifiant == "thomas.mart"
    assert doublon is True


def test_doublon_lettres_prenom_falls_back_to_suffix_when_template_has_no_prefix_var():
    engine = IdentifierEngine(format_key="prenom.nom", doublon_rule=DoublonRule.LETTRES_PRENOM)
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"thomas.martin"}
    )
    assert identifiant == "thomas.martin2"
    assert doublon is True


def test_doublon_annee_suffixe():
    engine = IdentifierEngine(
        format_key="prenom.nom", doublon_rule=DoublonRule.ANNEE_SUFFIXE, annee=2025
    )
    identifiant, doublon = engine.generate_unique(
        "Thomas", "Martin", existing_ids={"thomas.martin"}
    )
    assert identifiant == "thomas.martin2025"
    assert doublon is True


def test_no_doublon_returns_base():
    engine = IdentifierEngine(format_key="prenom.nom")
    identifiant, doublon = engine.generate_unique("Thomas", "Martin", existing_ids=set())
    assert identifiant == "thomas.martin"
    assert doublon is False


# sAMAccountName est limité à 20 caractères par AD lui-même (contrainte SAM
# pré-Windows 2000, non configurable) — un identifiant plus long est rejeté
# à la création avec une erreur AD peu explicite, découvert en testant contre
# un vrai AD ("Thérèse Müller-Özdemir" -> "therese.mullerozdemir", 21 car.).

def test_base_identifier_truncated_to_ad_sam_limit():
    engine = IdentifierEngine(format_key="prenom.nom")
    identifiant = engine.base_identifier("Thérèse", "Müller-Özdemir")
    assert identifiant == "therese.muller-ozdem"
    assert len(identifiant) <= 20


def test_doublon_numeric_suffix_stays_within_ad_sam_limit():
    engine = IdentifierEngine(format_key="prenom.nom")
    base = engine.base_identifier("Thérèse", "Müller-Özdemir")
    identifiant, doublon = engine.generate_unique(
        "Thérèse", "Müller-Özdemir", existing_ids={base}
    )
    assert doublon is True
    assert len(identifiant) <= 20
    # Le suffixe "2" ne doit jamais être coupé par la troncature (sinon
    # l'identifiant retomberait sur `base`, déjà pris).
    assert identifiant.endswith("2")
    assert identifiant != base
