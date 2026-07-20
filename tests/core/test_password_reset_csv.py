"""Tests de la fonction load_identifiers_csv du Module 5 et de la génération de mots
de passe en lot pour la réinitialisation."""

import tempfile
from pathlib import Path

import pytest

from edusync_ad.core.csv_io import has_identifier_column, load_identifiers_csv, load_names_csv
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import generate_password, generate_passwords_for_batch


# -- load_identifiers_csv -------------------------------------------------------------

def _write_csv(content: str, encoding: str = "utf-8") -> Path:
    tmp = Path(tempfile.mktemp(suffix=".csv"))
    tmp.write_text(content, encoding=encoding)
    return tmp


def testload_identifiers_csv_semicolon_delimiter():
    p = _write_csv("identifiant;nom\nthomas.martin;Martin\nalice.durand;Durand\n")
    ids = load_identifiers_csv(p)
    assert ids == ["thomas.martin", "alice.durand"]


def testload_identifiers_csv_comma_delimiter():
    p = _write_csv("identifiant,nom\nthomas.martin,Martin\n")
    ids = load_identifiers_csv(p)
    assert ids == ["thomas.martin"]


def testload_identifiers_csv_skips_empty_lines():
    p = _write_csv("identifiant\nthomas.martin\n\nalice.durand\n")
    ids = load_identifiers_csv(p)
    assert "" not in ids
    assert len(ids) == 2


def testload_identifiers_csv_latin1_encoding():
    p = _write_csv("identifiant\néléve.martin\n", encoding="latin-1")
    ids = load_identifiers_csv(p)
    assert len(ids) == 1


def testload_identifiers_csv_empty_file():
    p = _write_csv("")
    ids = load_identifiers_csv(p)
    assert ids == []


def testload_identifiers_csv_header_only():
    p = _write_csv("identifiant\n")
    ids = load_identifiers_csv(p)
    assert ids == []


def testload_identifiers_csv_detects_login_column():
    p = _write_csv("login;nom\nthomas.martin;Martin\n")
    ids = load_identifiers_csv(p)
    assert ids == ["thomas.martin"]


# -- has_identifier_column / load_names_csv (le personnel administratif ne fournit
# jamais d'identifiant AD, seulement prénom+nom) -------------------------------

def test_has_identifier_column_true_when_present():
    p = _write_csv("identifiant;nom\nthomas.martin;Martin\n")
    assert has_identifier_column(p) is True


def test_has_identifier_column_false_for_names_only():
    p = _write_csv("prenom;nom\nThomas;Martin\n")
    assert has_identifier_column(p) is False


def test_load_names_csv_extracts_prenom_nom_pairs():
    p = _write_csv("prenom;nom\nThomas;Martin\nAlice;Durand\n")
    assert load_names_csv(p) == [("Thomas", "Martin"), ("Alice", "Durand")]


def test_load_names_csv_returns_empty_without_prenom_nom_columns():
    p = _write_csv("identifiant\nthomas.martin\n")
    assert load_names_csv(p) == []


def test_load_names_csv_skips_incomplete_rows():
    p = _write_csv("prenom;nom\nThomas;Martin\n;Sansprenom\nAlice;\n")
    assert load_names_csv(p) == [("Thomas", "Martin")]


# -- génération de mots de passe en lot ---------------------------------------

def test_generate_passwords_batch_length():
    policy = PasswordPolicy(longueur=10, majuscules=True, chiffres=True)
    rows = [("Thomas", "Martin"), ("Alice", "Durand")]
    passwords = generate_passwords_for_batch(policy, rows)
    assert len(passwords) == 2
    for pwd in passwords:
        assert len(pwd) == 10


def test_generate_passwords_batch_identical_mode():
    policy = PasswordPolicy(longueur=8, mot_de_passe_identique=True)
    rows = [("a", "b"), ("c", "d"), ("e", "f")]
    passwords = generate_passwords_for_batch(policy, rows)
    assert len(set(passwords)) == 1


def test_generate_password_policy_enforces_uppercase():
    policy = PasswordPolicy(longueur=20, majuscules=True, chiffres=False, caracteres_speciaux=False)
    pwd = generate_password(policy)
    assert any(c.isupper() for c in pwd)


def test_generate_password_policy_enforces_digits():
    policy = PasswordPolicy(longueur=20, majuscules=False, chiffres=True, caracteres_speciaux=False)
    pwd = generate_password(policy)
    assert any(c.isdigit() for c in pwd)


def test_generate_password_policy_enforces_specials():
    policy = PasswordPolicy(longueur=20, majuscules=False, chiffres=False, caracteres_speciaux=True)
    pwd = generate_password(policy)
    specials = "!@#$%^&*-_+=?"
    assert any(c in specials for c in pwd)


def test_generate_password_respects_length():
    for length in (6, 10, 16, 32):
        policy = PasswordPolicy(longueur=length)
        pwd = generate_password(policy)
        assert len(pwd) == length
