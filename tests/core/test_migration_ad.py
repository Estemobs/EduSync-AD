"""Tests des nouvelles méthodes ADConnection pour la migration (Module 2)."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection, ConnectionState
from edusync_ad.core.ad.exceptions import ADError

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_4EMEA_DN = f"ou=4emeA,ou=eleves,{BASE_DN}"
OU_3EMEA_DN = f"ou=3emeA,ou=eleves,{BASE_DN}"
GROUP_4EMEA_DN = f"cn=4emeA,{OU_4EMEA_DN}"
GROUP_3EMEA_DN = f"cn=3emeA,{OU_3EMEA_DN}"
USER_DN = f"cn=Thomas Martin,{OU_4EMEA_DN}"
USER_SAM = "thomas.martin"


def make_mock_factory_with_user():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"})
    seed.strategy.add_entry(OU_4EMEA_DN, {"objectClass": "organizationalUnit", "ou": "4emeA"})
    seed.strategy.add_entry(OU_3EMEA_DN, {"objectClass": "organizationalUnit", "ou": "3emeA"})
    seed.strategy.add_entry(USER_DN, {"objectClass": "user", "sAMAccountName": USER_SAM, "cn": "Thomas Martin"})
    seed.strategy.add_entry(GROUP_4EMEA_DN, {"objectClass": "group", "sAMAccountName": "4emeA", "member": [USER_DN]})

    def factory(controller, bind_user, password, use_ssl):
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    conn = ADConnection(connection_factory=make_mock_factory_with_user())
    conn.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return conn


def test_search_user_by_sam_found(ad):
    result = ad.search_user_by_sam(USER_SAM, BASE_DN)
    assert result is not None
    dn, cn = result
    assert USER_SAM in dn.lower() or "thomas" in dn.lower()
    assert cn == "Thomas Martin"


def test_search_user_by_sam_not_found(ad):
    result = ad.search_user_by_sam("inconnu.user", BASE_DN)
    assert result is None


def test_remove_user_from_group(ad):
    assert ad.group_exists(GROUP_4EMEA_DN)
    ad.remove_user_from_group(USER_DN, GROUP_4EMEA_DN)
    # Le groupe existe encore (supprimer un membre ne supprime pas le groupe)
    assert ad.group_exists(GROUP_4EMEA_DN)


def test_move_user(ad):
    ad.move_user(USER_DN, OU_3EMEA_DN)
    new_dn = f"cn=Thomas Martin,{OU_3EMEA_DN}"
    result = ad.search_user_by_sam(USER_SAM, BASE_DN)
    assert result is not None
    dn, cn = result
    assert OU_3EMEA_DN.lower() in dn.lower()


def test_operations_require_connected_state_migration():
    ad = ADConnection(connection_factory=make_mock_factory_with_user())
    with pytest.raises(ADError):
        ad.search_user_by_sam(USER_SAM, BASE_DN)
    with pytest.raises(ADError):
        ad.move_user(USER_DN, OU_3EMEA_DN)
    with pytest.raises(ADError):
        ad.remove_user_from_group(USER_DN, GROUP_4EMEA_DN)
