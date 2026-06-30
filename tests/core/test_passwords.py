import string

from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import (
    generate_password,
    generate_passwords_for_batch,
    generate_random_password,
    render_password_pattern,
)


def test_render_password_pattern_preserves_literal_case_and_symbols():
    assert render_password_pattern("Ecole{AN}!", "Thomas", "Martin", year=2025) == "Ecole25!"


def test_render_password_pattern_supports_identifier_variables():
    assert (
        render_password_pattern("{P}.{n3}{ANNEE}", "Thomas", "Martin", year=2025)
        == "thomas.mar2025"
    )


def test_generate_random_password_respects_length():
    policy = PasswordPolicy(longueur=14, majuscules=True, chiffres=True, caracteres_speciaux=True)
    pwd = generate_random_password(policy)
    assert len(pwd) == 14


def test_generate_random_password_length_clamped_between_6_and_32():
    too_short = generate_random_password(PasswordPolicy(longueur=2))
    too_long = generate_random_password(PasswordPolicy(longueur=99))
    assert len(too_short) == 6
    assert len(too_long) == 32


def test_generate_random_password_enforces_required_categories():
    policy = PasswordPolicy(longueur=20, majuscules=True, chiffres=True, caracteres_speciaux=True)
    pwd = generate_random_password(policy)
    assert any(c in string.ascii_lowercase for c in pwd)
    assert any(c in string.ascii_uppercase for c in pwd)
    assert any(c in string.digits for c in pwd)
    assert any(not c.isalnum() for c in pwd)


def test_generate_random_password_no_optional_categories_is_lowercase_only():
    policy = PasswordPolicy(
        longueur=10, majuscules=False, chiffres=False, caracteres_speciaux=False
    )
    pwd = generate_random_password(policy)
    assert pwd == pwd.lower()
    assert pwd.isalpha()


def test_generate_password_uses_pattern_fixe_when_set():
    policy = PasswordPolicy(pattern_fixe="Ecole{AN}!")
    assert generate_password(policy, prenom="Thomas", nom="Martin", year=2025) == "Ecole25!"


def test_generate_passwords_for_batch_identical_option():
    policy = PasswordPolicy(longueur=10, mot_de_passe_identique=True)
    rows = [("Thomas", "Martin"), ("Lea", "Dupont"), ("Marc", "Petit")]
    passwords = generate_passwords_for_batch(policy, rows)
    assert len(set(passwords)) == 1
    assert len(passwords) == 3


def test_generate_passwords_for_batch_unique_by_default():
    policy = PasswordPolicy(longueur=12, mot_de_passe_identique=False)
    rows = [("Thomas", "Martin"), ("Lea", "Dupont"), ("Marc", "Petit")]
    passwords = generate_passwords_for_batch(policy, rows)
    # Génération aléatoire : très improbable d'avoir une collision sur 3 mots
    # de passe de 12 caractères.
    assert len(set(passwords)) == 3
