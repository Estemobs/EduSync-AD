"""Tests des méthodes ADConnection utilisées par le Module 6 (Explorateur AD) :
list_ous, list_groups, get_user_attributes, update_user_attribute, rename_user."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_ELEVES_DN = f"ou=eleves,{BASE_DN}"
OU_PROFS_DN = f"ou=professeurs,{BASE_DN}"
GROUP_DN = f"cn=3emeA,ou=groupes,{BASE_DN}"
USER_DN = f"cn=Thomas Martin,{OU_ELEVES_DN}"
USER_SAM = "thomas.martin"


def make_factory():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"})
    seed.strategy.add_entry(OU_ELEVES_DN, {"objectClass": "organizationalUnit", "ou": "eleves"})
    seed.strategy.add_entry(OU_PROFS_DN, {"objectClass": "organizationalUnit", "ou": "professeurs"})
    seed.strategy.add_entry(
        f"ou=groupes,{BASE_DN}", {"objectClass": "organizationalUnit", "ou": "groupes"}
    )
    seed.strategy.add_entry(
        GROUP_DN,
        {
            "objectClass": ["top", "group"],
            "sAMAccountName": "3emeA",
            "cn": "3emeA",
        },
    )
    seed.strategy.add_entry(
        USER_DN,
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "sAMAccountName": USER_SAM,
            "cn": "Thomas Martin",
            "givenName": "Thomas",
            "sn": "Martin",
            "displayName": "Thomas Martin",
            "mail": "thomas.martin@lycee.local",
            "userAccountControl": 512,
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


# -- list_ous ------------------------------------------------------------------

def test_list_ous_returns_all_ous(ad):
    ous = ad.list_ous(BASE_DN)
    ou_names = {name for _, name in ous}
    assert "eleves" in ou_names
    assert "professeurs" in ou_names


def test_list_ous_empty_under_unknown_base(ad):
    ous = ad.list_ous(f"ou=inexistant,{BASE_DN}")
    assert ous == []


def test_list_ous_requires_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.list_ous(BASE_DN)


# -- list_groups ---------------------------------------------------------------

def test_list_groups_returns_groups(ad):
    groups = ad.list_groups(BASE_DN)
    group_names = {name for _, name in groups}
    assert "3emeA" in group_names


def test_list_groups_empty_when_none(ad):
    # Aucun groupe sous cette OU
    groups = ad.list_groups(OU_ELEVES_DN)
    assert groups == []


def test_list_groups_requires_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.list_groups(BASE_DN)


# -- get_user_attributes -------------------------------------------------------

def test_get_user_attributes_returns_dict(ad):
    attrs = ad.get_user_attributes(USER_DN)
    assert attrs["sAMAccountName"] == USER_SAM
    assert attrs["displayName"] == "Thomas Martin"
    assert attrs["mail"] == "thomas.martin@lycee.local"
    assert attrs["disabled"] is False


def test_get_user_attributes_unknown_dn_returns_empty(ad):
    attrs = ad.get_user_attributes(f"cn=inconnu,{BASE_DN}")
    assert attrs == {}


def test_get_user_attributes_requires_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.get_user_attributes(USER_DN)


# -- update_user_attribute -----------------------------------------------------

def test_update_user_attribute_modifies_value(ad):
    ad.update_user_attribute(USER_DN, "description", "Classe 3ème A")
    attrs = ad.get_user_attributes(USER_DN)
    assert attrs.get("description") == "Classe 3ème A"


def test_update_user_attribute_dry_run_does_not_write(ad):
    ad.dry_run = True
    ad.update_user_attribute(USER_DN, "description", "Ne doit pas être écrit")
    # En dry_run l'appel ne lève pas et n'écrit rien
    attrs = ad.get_user_attributes(USER_DN)
    assert attrs.get("description", "") != "Ne doit pas être écrit"


def test_update_user_attribute_requires_connected():
    ad = ADConnection(connection_factory=make_factory())
    with pytest.raises(ADError):
        ad.update_user_attribute(USER_DN, "description", "test")


# -- enable_account / disable_account (toggle) ---------------------------------

def test_toggle_account_disable_then_enable(ad):
    ad.disable_account(USER_DN)
    # Après désactivation l'utilisateur reste présent
    result = ad.search_user_by_sam(USER_SAM, BASE_DN)
    assert result is not None

    ad.enable_account(USER_DN)
    result = ad.search_user_by_sam(USER_SAM, BASE_DN)
    assert result is not None


def test_toggle_account_dry_run_no_raise(ad):
    ad.dry_run = True
    ad.disable_account(USER_DN)
    ad.enable_account(USER_DN)
