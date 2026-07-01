"""Tests de la vérification de doublon AD pour le Module 4."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_DN = f"ou=3emeA,ou=eleves,{BASE_DN}"
EXISTING_USER_CN = "Thomas Martin"
EXISTING_DN = f"cn={EXISTING_USER_CN},{OU_DN}"


def make_factory():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"})
    seed.strategy.add_entry(OU_DN, {"objectClass": "organizationalUnit", "ou": "3emeA"})
    seed.strategy.add_entry(
        EXISTING_DN,
        {"objectClass": "user", "sAMAccountName": "thomas.martin", "cn": EXISTING_USER_CN},
    )

    def factory(controller, bind_user, password, use_ssl):
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    conn = ADConnection(connection_factory=make_factory())
    conn.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return conn


def test_search_user_by_cn_found(ad):
    result = ad.search_user_by_cn(EXISTING_USER_CN, OU_DN)
    assert result is not None
    assert "thomas" in result.lower() or "cn=" in result.lower()


def test_search_user_by_cn_not_found(ad):
    result = ad.search_user_by_cn("Inconnu Personne", OU_DN)
    assert result is None


def test_search_user_by_cn_wrong_ou(ad):
    other_ou = f"ou=4emeB,ou=eleves,{BASE_DN}"
    result = ad.search_user_by_cn(EXISTING_USER_CN, other_ou)
    assert result is None


def test_search_user_by_cn_requires_connected():
    from edusync_ad.core.ad.exceptions import ADError
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.search_user_by_cn(EXISTING_USER_CN, OU_DN)
