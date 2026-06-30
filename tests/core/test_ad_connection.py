"""Tests de la couche AD via la stratégie MOCK_SYNC de ldap3 — un serveur LDAP
simulé en mémoire, sans dépendance à un Active Directory réel."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection, ConnectionState
from edusync_ad.core.ad.exceptions import ADAuthError, ADError, ADUnreachableError

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
# MOCK_SYNC exige que le "user" de bind soit syntaxiquement un DN identique à
# celui d'une entrée existante. On utilise donc directement un nom déjà au
# format DN contenant un "@" (format_bind_user le laisse alors inchangé).
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_3EMEA_DN = f"ou=3emeA,ou=eleves,{BASE_DN}"
GROUP_3EMEA_DN = f"cn=3emeA,ou=groupes,{BASE_DN}"


def make_mock_connection_factory():
    """Construit une factory de connexion ldap3 branchée sur un serveur MOCK_SYNC
    pré-peuplé, compatible avec l'interface attendue par ADConnection."""
    server = Server("mock-server")
    seed_conn = Connection(server, client_strategy=MOCK_SYNC)
    seed_conn.strategy.add_entry(
        ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"}
    )
    seed_conn.strategy.add_entry(
        OU_3EMEA_DN, {"objectClass": "organizationalUnit", "ou": "3emeA"}
    )
    seed_conn.strategy.add_entry(
        f"cn=Existing User,{OU_3EMEA_DN}",
        {"objectClass": "user", "sAMAccountName": "existing.user"},
    )

    def factory(controller: str, bind_user: str, password: str, use_ssl: bool) -> Connection:
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    return ADConnection(connection_factory=make_mock_connection_factory())


def test_connect_success(ad):
    result = ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    assert result.state == ConnectionState.CONNECTED
    assert ad.state == ConnectionState.CONNECTED
    assert result.warning is None


def test_connect_wrong_password_raises_auth_error(ad):
    with pytest.raises(ADAuthError):
        ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, "wrong-password")
    assert ad.state == ConnectionState.DISCONNECTED


def test_ldaps_unreachable_falls_back_to_ldap_with_warning(mocker):
    ad = ADConnection(connection_factory=make_mock_connection_factory())
    real_try_bind = ad._try_bind
    calls = []

    def fake_try_bind(controller, bind_user, password, *, use_ssl):
        calls.append(use_ssl)
        if use_ssl:
            raise ADUnreachableError("LDAPS indisponible (simulé)")
        return real_try_bind(controller, bind_user, password, use_ssl=False)

    mocker.patch.object(ad, "_try_bind", side_effect=fake_try_bind)
    result = ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)

    assert calls == [True, False]
    assert result.used_ldaps is False
    assert result.warning is not None
    assert ad.state == ConnectionState.CONNECTED


def test_operations_require_connected_state():
    ad = ADConnection(connection_factory=make_mock_connection_factory())
    with pytest.raises(ADError):
        ad.search_existing_identifiers(BASE_DN)


def test_search_existing_identifiers(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    identifiers = ad.search_existing_identifiers(OU_3EMEA_DN)
    assert identifiers == {"existing.user"}


def test_ou_exists(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    assert ad.ou_exists(OU_3EMEA_DN) is True
    assert ad.ou_exists(f"ou=inexistante,{BASE_DN}") is False


def test_group_exists_false_when_absent(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    assert ad.group_exists(GROUP_3EMEA_DN) is False


def test_create_user_then_visible_in_search(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    new_dn = f"cn=Thomas Martin,{OU_3EMEA_DN}"
    ad.create_user(new_dn, {"sAMAccountName": "thomas.martin"})
    identifiers = ad.search_existing_identifiers(OU_3EMEA_DN)
    assert "thomas.martin" in identifiers


def test_create_group_and_add_user(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    ad.create_group(GROUP_3EMEA_DN, "3emeA")
    assert ad.group_exists(GROUP_3EMEA_DN) is True

    user_dn = f"cn=Thomas Martin,{OU_3EMEA_DN}"
    ad.create_user(user_dn, {"sAMAccountName": "thomas.martin"})
    ad.add_user_to_group(user_dn, GROUP_3EMEA_DN)  # ne doit pas lever


def test_dry_run_does_not_write_to_ad(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    ad.dry_run = True

    new_dn = f"cn=Simulated User,{OU_3EMEA_DN}"
    ad.create_user(new_dn, {"sAMAccountName": "simulated.user"})
    ad.create_group(GROUP_3EMEA_DN, "3emeA")
    ad.add_user_to_group(new_dn, GROUP_3EMEA_DN)

    identifiers = ad.search_existing_identifiers(OU_3EMEA_DN)
    assert "simulated.user" not in identifiers
    assert ad.group_exists(GROUP_3EMEA_DN) is False


def test_dry_run_still_allows_reads(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    ad.dry_run = True
    identifiers = ad.search_existing_identifiers(OU_3EMEA_DN)
    assert identifiers == {"existing.user"}


def test_create_user_with_password_sets_password_and_enables_account(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    new_dn = f"cn=Thomas Martin,{OU_3EMEA_DN}"
    ad.create_user(new_dn, {"sAMAccountName": "thomas.martin"}, password="NewPass123!")
    identifiers = ad.search_existing_identifiers(OU_3EMEA_DN)
    assert "thomas.martin" in identifiers


def test_domain_to_base_dn():
    assert ADConnection.domain_to_base_dn("lycee-victor-hugo.local") == (
        "DC=lycee-victor-hugo,DC=local"
    )


def test_create_user_with_password_dry_run_does_not_call_ad(ad):
    ad.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    ad.dry_run = True
    new_dn = f"cn=Simulated User,{OU_3EMEA_DN}"
    ad.create_user(new_dn, {"sAMAccountName": "simulated.user"}, password="NewPass123!")
    identifiers = ad.search_existing_identifiers(OU_3EMEA_DN)
    assert "simulated.user" not in identifiers
