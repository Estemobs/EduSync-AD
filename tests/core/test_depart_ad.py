"""Tests des méthodes ADConnection pour la gestion des départs (Module 3)."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection, UAC_NORMAL_ACCOUNT_DISABLED
from edusync_ad.core.ad.exceptions import ADError

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_DN = f"ou=3emeA,ou=eleves,{BASE_DN}"
USER_DN = f"cn=Thomas Martin,{OU_DN}"
USER_SAM = "thomas.martin"
GROUP_DN = f"cn=3emeA,{OU_DN}"


def make_factory():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"})
    seed.strategy.add_entry(OU_DN, {"objectClass": "organizationalUnit", "ou": "3emeA"})
    seed.strategy.add_entry(
        USER_DN,
        {"objectClass": "user", "sAMAccountName": USER_SAM, "cn": "Thomas Martin", "userAccountControl": 512},
    )
    seed.strategy.add_entry(
        GROUP_DN,
        {"objectClass": "group", "sAMAccountName": "3emeA", "member": [USER_DN]},
    )

    def factory(controller, bind_user, password, use_ssl):
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    conn = ADConnection(connection_factory=make_factory())
    conn.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return conn


def test_disable_account(ad):
    ad.disable_account(USER_DN)
    # L'utilisateur est toujours trouvable après désactivation
    result = ad.search_user_by_sam(USER_SAM, BASE_DN)
    assert result is not None


def test_delete_user(ad):
    ad.delete_user(USER_DN)
    result = ad.search_user_by_sam(USER_SAM, BASE_DN)
    assert result is None


def test_search_user_groups(ad):
    groups = ad.search_user_groups(USER_DN, BASE_DN)
    assert any("3emea" in g.lower() or "3emeA" in g for g in groups)


def test_search_user_groups_none_when_no_group(ad):
    # Un utilisateur qui n'est dans aucun groupe
    other_dn = f"cn=Jane Doe,{OU_DN}"
    ad._conn.add(other_dn, ["top", "person", "organizationalPerson", "user"], {"sAMAccountName": "jane.doe"})
    groups = ad.search_user_groups(other_dn, BASE_DN)
    assert groups == []


def test_operations_require_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.disable_account(USER_DN)
    with pytest.raises(ADError):
        ad.delete_user(USER_DN)
    with pytest.raises(ADError):
        ad.search_user_groups(USER_DN, BASE_DN)


# -- Tests pour la file d'attente pending_deletions dans AuditLog -----------

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from edusync_ad.core.audit import AuditLog


def _make_audit():
    tmp = tempfile.mktemp(suffix=".db")
    return AuditLog(Path(tmp))


def test_add_and_get_pending_deletions():
    log = _make_audit()
    log.add_pending_deletion("CN=test,DC=x", "test.user", "Test User", "sess1", delai_jours=30)
    pending = log.get_pending_deletions()
    assert len(pending) == 1
    assert pending[0]["sam_account_name"] == "test.user"


def test_count_due_deletions_zero_when_recent():
    log = _make_audit()
    log.add_pending_deletion("CN=test,DC=x", "test.user", "Test User", "sess1", delai_jours=30)
    # Délai 30 jours — l'entrée vient d'être ajoutée → pas encore échue
    assert log.count_due_deletions(30) == 0


def test_count_due_deletions_nonzero_when_past_delay():
    log = _make_audit()
    # Insérer une entrée avec moved_at dans le passé (31 jours)
    past_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
    log._conn.execute(
        "INSERT INTO pending_deletions (user_dn, sam_account_name, nom_complet, moved_at, session_id) VALUES (?,?,?,?,?)",
        ("CN=old,DC=x", "old.user", "Old User", past_ts, "sess2"),
    )
    log._conn.commit()
    assert log.count_due_deletions(30) == 1


def test_due_deletion_uses_per_entry_delai_not_global_default():
    log = _make_audit()
    # Archivé il y a 10 jours avec un délai court (5 jours) choisi pour ce lot
    # → doit être échu même si le délai par défaut global est plus long (30).
    past_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    log._conn.execute(
        "INSERT INTO pending_deletions "
        "(user_dn, sam_account_name, nom_complet, moved_at, session_id, delai_jours) "
        "VALUES (?,?,?,?,?,?)",
        ("CN=short,DC=x", "short.user", "Short User", past_ts, "sess3", 5),
    )
    log._conn.commit()
    due = log.get_due_deletions(default_delai_jours=30)
    assert {d["sam_account_name"] for d in due} == {"short.user"}


def test_remove_pending_deletion():
    log = _make_audit()
    log.add_pending_deletion("CN=test,DC=x", "test.user", "Test User", "sess1", delai_jours=30)
    log.remove_pending_deletion("CN=test,DC=x")
    assert log.get_pending_deletions() == []
