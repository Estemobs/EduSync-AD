import json
import os
import stat

import pytest

from edusync_ad.core.audit import AuditLog, new_session_id
from edusync_ad.core.crypto import save_remembered_connection, RememberedConnection
from edusync_ad.core.multisite import (
    DomainProfile,
    MultisiteError,
    ensure_sites,
    find_profile,
    import_legacy_connection,
    load_sites,
    new_profile,
    profile_for_domain,
    save_sites,
    unique_site_id,
)


def _sample() -> DomainProfile:
    return DomainProfile(
        id="aaaa1111",
        label="Lycée Victor Hugo",
        domain="lycee-victor-hugo.local",
        controller="10.0.0.5",
        username="admin",
        password="S3cret!",
        remember_password=True,
        verify_certificate=False,
        ca_cert_path="/etc/ssl/ca.pem",
    )


# -- Identifiants -----------------------------------------------------------


def test_unique_site_id_never_collides():
    existing = [DomainProfile(id="deadbeef", label="a", domain="a.local")]
    for _ in range(50):
        site_id = unique_site_id(existing)
        assert site_id
        assert site_id not in {profile.id for profile in existing}
        existing.append(DomainProfile(id=site_id, label="x", domain="x.local"))


def test_new_profile_generates_id_and_defaults():
    profile = new_profile("Site", "college-a.local", username="admin")
    assert profile.id
    assert profile.verify_certificate is True
    assert profile.remember_password is False
    assert profile.stored_password == ""


# -- Validation -------------------------------------------------------------


def test_validate_accepts_a_valid_profile():
    assert _sample().validate() == []


def test_validate_requires_label_and_domain():
    profile = DomainProfile(id="x", label="", domain="")
    errors = profile.validate()
    assert len(errors) == 2


def test_validate_rejects_malformed_domain():
    profile = DomainProfile(id="x", label="Site", domain="domaine invalide!")
    assert any("invalide" in error for error in profile.validate())


def test_validate_requires_password_when_remembering():
    profile = DomainProfile(id="x", label="S", domain="a.local", remember_password=True)
    assert any("mot de passe" in error for error in profile.validate())


# -- Persistance chiffrée ---------------------------------------------------


def test_sites_round_trip(tmp_path):
    path = tmp_path / "domaines.json"
    key_path = tmp_path / "secret.key"
    save_sites([_sample()], path, key_path=key_path)

    loaded = load_sites(path, key_path=key_path)
    assert loaded == [_sample()]


def test_sites_file_is_encrypted(tmp_path):
    path = tmp_path / "domaines.json"
    save_sites([_sample()], path, key_path=tmp_path / "secret.key")

    raw = path.read_text(encoding="utf-8")
    assert "lycee-victor-hugo.local" not in raw
    assert "S3cret!" not in raw
    envelope = json.loads(raw)
    assert set(envelope) == {"version", "payload"}


@pytest.mark.skipif(os.name != "posix", reason="permissions POSIX uniquement")
def test_sites_file_is_private(tmp_path):
    path = tmp_path / "domaines.json"
    save_sites([_sample()], path, key_path=tmp_path / "secret.key")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_password_dropped_when_not_remembered(tmp_path):
    path = tmp_path / "domaines.json"
    key_path = tmp_path / "secret.key"
    profile = _sample()
    profile.remember_password = False

    save_sites([profile], path, key_path=key_path)
    loaded = load_sites(path, key_path=key_path)
    assert loaded[0].password == ""


def test_load_missing_file_returns_empty(tmp_path):
    assert load_sites(tmp_path / "domaines.json", key_path=tmp_path / "secret.key") == []


def test_load_corrupt_file_raises(tmp_path):
    path = tmp_path / "domaines.json"
    path.write_text('{"version": 1, "payload": "pas-du-base64!"}', encoding="utf-8")
    with pytest.raises(MultisiteError):
        load_sites(path, key_path=tmp_path / "secret.key")


def test_ensure_sites_never_raises_on_corrupt_file(tmp_path):
    path = tmp_path / "domaines.json"
    path.write_text("{cassé", encoding="utf-8")
    assert ensure_sites(path, key_path=tmp_path / "secret.key") == []


def test_save_normalizes_domain_case(tmp_path):
    path = tmp_path / "domaines.json"
    profile = DomainProfile(id="id1", label="S", domain="  LYCEE-VICTOR-HUGO.LOCAL ")
    save_sites([profile], path, key_path=tmp_path / "secret.key")
    assert load_sites(path, key_path=tmp_path / "secret.key")[0].domain == "lycee-victor-hugo.local"


# -- Recherche --------------------------------------------------------------


def test_find_profile_and_profile_for_domain():
    profiles = [_sample(), DomainProfile(id="bbbb2222", label="B", domain="b.local")]
    assert find_profile(profiles, "bbbb2222").label == "B"
    assert find_profile(profiles, "inconnu") is None
    assert find_profile(profiles, None) is None
    assert profile_for_domain(profiles, "LYCEE-VICTOR-HUGO.local").id == "aaaa1111"
    assert profile_for_domain(profiles, "autre.local") is None
    assert profile_for_domain(profiles, None) is None


# -- Migration de connection.local.json (M9 → M25) --------------------------


def _write_legacy(tmp_path):
    legacy = tmp_path / "connection.local.json"
    save_remembered_connection(
        RememberedConnection(
            domaine="lycee-victor-hugo.local",
            controleur="dc01.lycee-victor-hugo.local",
            utilisateur="admin",
            mot_de_passe="Bienvenue01",
        ),
        key_path=tmp_path / "secret.key",
        path=legacy,
    )
    return legacy


def test_import_legacy_connection(tmp_path):
    legacy = _write_legacy(tmp_path)
    profile = import_legacy_connection(
        tmp_path / "domaines.json", key_path=tmp_path / "secret.key", legacy_path=legacy
    )
    assert profile is not None
    assert profile.domain == "lycee-victor-hugo.local"
    assert profile.controller == "dc01.lycee-victor-hugo.local"
    assert profile.username == "admin"
    assert profile.password == "Bienvenue01"
    assert profile.remember_password is True


def test_import_legacy_connection_without_password(tmp_path):
    legacy = tmp_path / "connection.local.json"
    save_remembered_connection(
        RememberedConnection(domaine="d.local", controleur="c", utilisateur="u"),
        key_path=tmp_path / "secret.key",
        path=legacy,
    )
    profile = import_legacy_connection(
        tmp_path / "domaines.json", key_path=tmp_path / "secret.key", legacy_path=legacy
    )
    assert profile is not None
    assert profile.password == ""
    assert profile.remember_password is False


def test_import_legacy_connection_no_file(tmp_path):
    assert (
        import_legacy_connection(
            tmp_path / "domaines.json",
            key_path=tmp_path / "secret.key",
            legacy_path=tmp_path / "absent.json",
        )
        is None
    )


def test_ensure_sites_migrates_legacy_once(tmp_path):
    legacy = _write_legacy(tmp_path)
    path = tmp_path / "domaines.json"
    key_path = tmp_path / "secret.key"

    first = ensure_sites(path, key_path=key_path, legacy_path=legacy)
    assert len(first) == 1
    assert path.exists()

    second = ensure_sites(path, key_path=key_path, legacy_path=legacy)
    assert [profile.id for profile in second] == [profile.id for profile in first]


def test_ensure_sites_empty_without_any_source(tmp_path):
    assert ensure_sites(
        tmp_path / "domaines.json",
        key_path=tmp_path / "secret.key",
        legacy_path=tmp_path / "absent.json",
    ) == []


# -- Journal d'audit séparé par domaine (M25) -------------------------------


def test_record_stamps_current_domain(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    log.current_domain = "lycee-victor-hugo.local"
    log.record("creation_compte", "thomas.martin", "succes", new_session_id())

    assert log.query()[0].domaine == "lycee-victor-hugo.local"
    assert log.domains() == ["lycee-victor-hugo.local"]


def test_query_filters_by_domain(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()
    log.current_domain = "a.local"
    log.record("creation_compte", "a", "succes", session)
    log.current_domain = "b.local"
    log.record("creation_compte", "b", "succes", session)

    assert [entry.compte for entry in log.query(domaine="a.local")] == ["a"]
    assert [entry.compte for entry in log.query(domaine="b.local")] == ["b"]
    assert sorted(log.domains()) == ["a.local", "b.local"]


def test_export_csv_contains_domain(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    log.current_domain = "college-b.local"
    log.record("migration_compte", "lea.dupont", "succes", new_session_id())

    out = tmp_path / "journal.csv"
    log.export_csv(out)
    content = out.read_text(encoding="utf-8-sig")
    assert "domaine" in content.splitlines()[0]
    assert "college-b.local" in content


def test_legacy_db_gains_domain_column(tmp_path):
    """Une base créée avant M25 est migrée par ALTER TABLE au premier ouverture."""
    import sqlite3

    db_path = tmp_path / "journal.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action_type TEXT NOT NULL,
            compte TEXT NOT NULL,
            ou_source TEXT,
            ou_destination TEXT,
            resultat TEXT NOT NULL,
            session_id TEXT NOT NULL,
            simulation INTEGER NOT NULL,
            detail TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO actions (timestamp, action_type, compte, resultat, session_id, "
        "simulation) VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-01-01T00:00:00+00:00", "creation_compte", "ancien", "succes", "sid", 0),
    )
    conn.commit()
    conn.close()

    log = AuditLog(db_path)
    entries = log.query()
    assert len(entries) == 1
    assert entries[0].compte == "ancien"
    assert entries[0].domaine == ""
