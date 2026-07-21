"""Tests du coffre-fort local des mots de passe (chiffrement AES-256-GCM)."""

from edusync_ad.core.password_vault import PasswordVault


def _vault(tmp_path) -> PasswordVault:
    return PasswordVault(tmp_path / "vault.sqlite3", key_path=tmp_path / "secret.key")


def test_store_and_get_round_trip(tmp_path):
    vault = _vault(tmp_path)
    vault.store("thomas.martin", "Sup3r$ecret!")
    assert vault.get("thomas.martin") == "Sup3r$ecret!"


def test_get_unknown_sam_returns_none(tmp_path):
    vault = _vault(tmp_path)
    assert vault.get("inconnu") is None


def test_sam_lookup_is_case_insensitive(tmp_path):
    vault = _vault(tmp_path)
    vault.store("Thomas.Martin", "abc123")
    assert vault.get("thomas.martin") == "abc123"
    assert vault.has("THOMAS.MARTIN")


def test_store_overwrites_previous_password(tmp_path):
    vault = _vault(tmp_path)
    vault.store("thomas.martin", "premier")
    vault.store("thomas.martin", "second")
    assert vault.get("thomas.martin") == "second"


def test_delete_removes_entry(tmp_path):
    vault = _vault(tmp_path)
    vault.store("thomas.martin", "abc123")
    vault.delete("thomas.martin")
    assert vault.get("thomas.martin") is None
    assert not vault.has("thomas.martin")


def test_password_stored_on_disk_is_never_plaintext(tmp_path):
    vault = _vault(tmp_path)
    vault.store("thomas.martin", "MotDePasseTresSecret")
    raw = vault.path.read_bytes()
    assert b"MotDePasseTresSecret" not in raw


def test_clear_all_removes_everything_and_returns_count(tmp_path):
    vault = _vault(tmp_path)
    vault.store("eleve1", "a")
    vault.store("eleve2", "b")
    removed = vault.clear_all()
    assert removed == 2
    assert vault.count() == 0
    assert vault.get("eleve1") is None


def test_store_ignores_empty_sam_or_password(tmp_path):
    vault = _vault(tmp_path)
    vault.store("", "abc123")
    vault.store("thomas.martin", "")
    assert vault.count() == 0
