"""Tests des méthodes ADConnection utilisées par le Module 5 (réinitialisation
de mot de passe en masse) : list_users_in_ou, list_users_in_group."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_ELEVES_DN = f"ou=eleves,{BASE_DN}"
OU_3EMEA_DN = f"ou=3emeA,{OU_ELEVES_DN}"
GROUP_DN = f"cn=3emeA,ou=groupes,{BASE_DN}"
USER1_DN = f"cn=Thomas Martin,{OU_3EMEA_DN}"
USER2_DN = f"cn=Alice Durand,{OU_3EMEA_DN}"


def make_factory():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"})
    seed.strategy.add_entry(OU_ELEVES_DN, {"objectClass": "organizationalUnit", "ou": "eleves"})
    seed.strategy.add_entry(OU_3EMEA_DN, {"objectClass": "organizationalUnit", "ou": "3emeA"})
    seed.strategy.add_entry(
        USER1_DN,
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "objectCategory": "person",
            "sAMAccountName": "thomas.martin",
            "cn": "Thomas Martin",
            "userAccountControl": 512,
            # MOCK_SYNC ne calcule pas automatiquement le backlink memberOf à
            # partir du "member" du groupe (contrairement à un vrai AD) — on
            # le pose explicitement pour que le mock reflète un AD réel.
            "memberOf": [GROUP_DN],
        },
    )
    seed.strategy.add_entry(
        USER2_DN,
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "objectCategory": "person",
            "sAMAccountName": "alice.durand",
            "cn": "Alice Durand",
            "userAccountControl": 514,  # désactivé
        },
    )
    seed.strategy.add_entry(
        GROUP_DN,
        {
            "objectClass": ["top", "group"],
            "sAMAccountName": "3emeA",
            "cn": "3emeA",
            "member": [USER1_DN],
        },
    )

    def factory(controller, bind_user, password, use_ssl):
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    conn = ADConnection(connection_factory=make_factory())
    conn.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return conn


# -- list_users_in_ou ----------------------------------------------------------

def test_list_users_in_ou_returns_users(ad):
    users = ad.list_users_in_ou(OU_3EMEA_DN)
    sams = {u["sam"] for u in users}
    assert "thomas.martin" in sams
    assert "alice.durand" in sams


def test_list_users_in_ou_empty_ou(ad):
    # OU qui n'existe pas → liste vide (pas d'exception)
    users = ad.list_users_in_ou(f"ou=vide,{BASE_DN}")
    assert users == []


def test_list_users_in_ou_requires_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.list_users_in_ou(OU_3EMEA_DN)


# -- list_users_in_group -------------------------------------------------------

def test_list_users_in_group_returns_members(ad):
    users = ad.list_users_in_group(GROUP_DN, BASE_DN)
    sams = {u["sam"] for u in users}
    assert "thomas.martin" in sams


def test_list_users_in_group_empty_when_group_absent(ad):
    users = ad.list_users_in_group(f"cn=inexistant,{BASE_DN}", BASE_DN)
    assert users == []


def test_list_users_in_group_requires_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.list_users_in_group(GROUP_DN, BASE_DN)


# -- set_password dry_run ------------------------------------------------------

def test_set_password_dry_run_does_not_raise(ad):
    ad.dry_run = True
    # MOCK_SYNC ne supporte pas extend.microsoft, mais dry_run court-circuite l'appel
    ad.set_password(USER1_DN, "NouveauMdP123!")  # ne doit pas lever
