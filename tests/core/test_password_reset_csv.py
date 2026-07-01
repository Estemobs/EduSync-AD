"""Tests de la fonction _load_ids_csv du Module 5 et de la génération de mots
de passe en lot pour la réinitialisation."""

import tempfile
from pathlib import Path

import pytest

from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import generate_password, generate_passwords_for_batch
from edusync_ad.ui.modules.password_reset_page import _load_ids_csv


# -- _load_ids_csv -------------------------------------------------------------

def _write_csv(content: str, encoding: str = "utf-8") -> Path:
    tmp = Path(tempfile.mktemp(suffix=".csv"))
    tmp.write_text(content, encoding=encoding)
    return tmp


def test_load_ids_csv_semicolon_delimiter():
    p = _write_csv("identifiant;nom\nthomas.martin;Martin\nalice.durand;Durand\n")
    ids = _load_ids_csv(p)
    assert ids == ["thomas.martin", "alice.durand"]


def test_load_ids_csv_comma_delimiter():
    p = _write_csv("identifiant,nom\nthomas.martin,Martin\n")
    ids = _load_ids_csv(p)
    assert ids == ["thomas.martin"]


def test_load_ids_csv_skips_empty_lines():
    p = _write_csv("identifiant\nthomas.martin\n\nalice.durand\n")
    ids = _load_ids_csv(p)
    assert "" not in ids
    assert len(ids) == 2


def test_load_ids_csv_latin1_encoding():
    p = _write_csv("identifiant\néléve.martin\n", encoding="latin-1")
    ids = _load_ids_csv(p)
    assert len(ids) == 1


def test_load_ids_csv_empty_file():
    p = _write_csv("")
    ids = _load_ids_csv(p)
    assert ids == []


def test_load_ids_csv_header_only():
    p = _write_csv("identifiant\n")
    ids = _load_ids_csv(p)
    assert ids == []


def test_load_ids_csv_detects_login_column():
    p = _write_csv("login;nom\nthomas.martin;Martin\n")
    ids = _load_ids_csv(p)
    assert ids == ["thomas.martin"]


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
