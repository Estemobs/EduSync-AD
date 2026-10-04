"""M26 — Délégation d'administration (RBAC).

Cinq rôles, une matrice de permissions, des portées par OU/groupe, un
garde-fou au niveau de la connexion AD (seul point de sortie LDAP) et un
journal d'audit filtrable par opérateur.
"""

import json

import pytest

from edusync_ad.core.ad.connection import ADConnection, _enforce_rbac
from edusync_ad.core.ad.exceptions import ADInsufficientRightsError
from edusync_ad.core.audit import AuditLog, new_session_id
from edusync_ad.core.rbac import (
    PERMISSIONS,
    READ_PERMISSIONS,
    ROLE_LABELS,
    ROLE_ORDER,
    ROLE_PERMISSIONS,
    SCOPED_ROLES,
    OperatorGrant,
    RBACPolicy,
    Role,
    build_policy,
    dn_in_scopes,
    find_grant,
    load_grants,
    matching_grants,
    normalize_dn,
    role_label,
    save_grants,
    unique_grant_id,
)
from tests.core.test_ad_connection import (
    ADMIN_BIND_DN,
    ADMIN_PASSWORD,
    BASE_DN,
    DOMAIN,
    OU_3EMEA_DN,
    make_mock_connection_factory,
)

OU_A = "OU=3emeA,OU=eleves,DC=lycee,DC=local"
OU_B = "OU=3emeB,OU=eleves,DC=lycee,DC=local"
GROUP_A = "CN=3emeA,OU=groupes,DC=lycee,DC=local"


def _grant(**kwargs) -> OperatorGrant:
    kwargs.setdefault("id", unique_grant_id())
    kwargs.setdefault("operateur", "alice")
    return OperatorGrant(**kwargs)


# -- Matrice des rôles -------------------------------------------------------


def test_role_matrix_covers_the_five_documented_roles():
    assert [role.value for role in ROLE_ORDER] == [
        "super_admin", "admin_site", "admin_classe", "helpdesk", "lecture_seule",
    ]
    assert set(ROLE_PERMISSIONS) == set(ROLE_ORDER)
    assert set(ROLE_LABELS) == set(ROLE_ORDER)
    for role in ROLE_ORDER:
        # Aucune permission inventée : tout vient du vocabulaire déclaré.
        assert ROLE_PERMISSIONS[role] <= set(PERMISSIONS)
        # Tout rôle, même lecture seule, doit pouvoir consulter.
        assert ROLE_PERMISSIONS[role] & READ_PERMISSIONS


def test_super_admin_holds_every_permission():
    assert ROLE_PERMISSIONS[Role.SUPER_ADMIN] == frozenset(PERMISSIONS)


def test_admin_site_holds_everything_except_delegation_administration():
    assert ROLE_PERMISSIONS[Role.ADMIN_SITE] == frozenset(PERMISSIONS) - {"admin"}


def test_read_only_role_only_reads():
    assert ROLE_PERMISSIONS[Role.LECTURE_SEULE] == READ_PERMISSIONS


def test_only_super_admin_manages_the_delegation():
    assert [role for role in ROLE_ORDER if "admin" in ROLE_PERMISSIONS[role]] == [
        Role.SUPER_ADMIN
    ]


def test_scoped_roles_never_administer_nor_delete():
    for role in SCOPED_ROLES:
        perms = ROLE_PERMISSIONS[role]
        assert "admin" not in perms
        assert "delete_user" not in perms
        assert "delete_ou" not in perms


def test_every_role_that_creates_accounts_can_finish_the_lifecycle():
    """``create_user`` rappelle ``set_password`` puis ``enable_account`` : un
    rôle ayant ``create_user`` sans ``reset_password``/``disable_account``
    échouerait à mi-parcours et laisserait un compte orphelin."""
    for role in ROLE_ORDER:
        perms = ROLE_PERMISSIONS[role]
        if "create_user" in perms:
            assert {"reset_password", "disable_account"} <= perms, role


def test_helpdesk_resets_but_never_creates():
    perms = ROLE_PERMISSIONS[Role.HELPDESK]
    assert {"reset_password", "disable_account", "modify_user"} <= perms
    assert "create_user" not in perms
    assert "delete_user" not in perms


def test_role_label_is_the_french_caption():
    assert role_label(Role.ADMIN_CLASSE) == "Admin classe"
    assert _grant(role=Role.HELPDESK).role_label == "Helpdesk"


# -- Résolution opérateur → rôle --------------------------------------------


def test_no_grant_at_all_keeps_the_historical_full_access():
    policy = build_policy("admin", "lycee.local", grants=[])
    assert policy.role is Role.SUPER_ADMIN
    assert policy.can_manage_delegation
    assert "aucun délégué" in policy.source


def test_operator_absent_from_an_existing_delegation_is_read_only():
    grants = [_grant(operateur="alice", role=Role.ADMIN_SITE)]
    policy = build_policy("bob", "lycee.local", grants)
    assert policy.role is Role.LECTURE_SEULE
    assert not policy.has("create_user")
    assert not policy.can_manage_delegation
    assert "non référencé" in policy.source


def test_inactive_grants_restrict_nobody():
    grants = [_grant(operateur="alice", role=Role.ADMIN_SITE, actif=False)]
    assert build_policy("alice", "lycee.local", grants).role is Role.SUPER_ADMIN
    assert build_policy("bob", "lycee.local", grants).role is Role.SUPER_ADMIN


def test_matching_grant_applies_role_and_scopes():
    grants = [_grant(role=Role.ADMIN_CLASSE, ous=[OU_A])]
    policy = build_policy("alice", "lycee.local", grants)
    assert policy.role is Role.ADMIN_CLASSE
    assert policy.ous == [OU_A]
    assert policy.is_scoped
    assert not policy.readonly
    assert "alice" in policy.source


def test_operator_name_is_normalized_regardless_of_format():
    grants = [_grant(operateur="LYCEE\\alice", role=Role.HELPDESK, ous=[OU_A])]
    for name in ("alice", "ALICE", " alice ", "lycee\\alice", "alice@lycee.local"):
        assert build_policy(name, "lycee.local", grants).role is Role.HELPDESK, name


def test_grant_scoped_to_another_site_does_not_apply():
    grants = [
        _grant(operateur="alice", role=Role.ADMIN_SITE, site="college-b.local"),
        _grant(operateur="bob", role=Role.HELPDESK, ous=[OU_A]),  # site vide = tous sites
    ]
    assert build_policy("alice", "lycee.local", grants).role is Role.LECTURE_SEULE
    assert build_policy("alice", "", grants).role is Role.LECTURE_SEULE
    assert build_policy("alice", "COLLEGE-B.LOCAL", grants).role is Role.ADMIN_SITE
    assert build_policy("bob", "autre.local", grants).role is Role.HELPDESK


def test_matching_grants_filters_on_role_site_and_activation():
    grants = [
        _grant(id="g1", operateur="alice", role=Role.ADMIN_SITE, site="b.local"),
        _grant(id="g2", operateur="alice", role=Role.HELPDESK, ous=[OU_A]),
        _grant(id="g3", operateur="alice", role=Role.LECTURE_SEULE, actif=False),
    ]
    matched = matching_grants(grants, "alice", "lycee.local")
    assert [grant.id for grant in matched] == ["g2"]
    assert matching_grants(grants, "charlie", "lycee.local") == []


def test_cumulated_grants_keep_the_widest_role_and_union_of_scopes():
    grants = [
        _grant(id="g1", operateur="alice", role=Role.HELPDESK, ous=[OU_A], groupes=[GROUP_A]),
        _grant(id="g2", operateur="alice", role=Role.ADMIN_CLASSE, ous=[OU_B]),
    ]
    policy = build_policy("alice", "lycee.local", grants)
    assert policy.role is Role.ADMIN_CLASSE  # plus large que helpdesk
    assert set(policy.ous) == {OU_A, OU_B}
    assert policy.groupes == [GROUP_A]


# -- Portées -----------------------------------------------------------------


def test_normalize_dn_strips_spacing_and_case():
    assert normalize_dn(" OU = 3emeA , OU = eleves , DC=lycee , DC=local ") == (
        "ou=3emea,ou=eleves,dc=lycee,dc=local"
    )
    assert normalize_dn("") == ""


def test_dn_in_scopes_covers_the_whole_subtree():
    assert dn_in_scopes(OU_A, [OU_A])
    assert dn_in_scopes(f"cn=thomas.martin,{OU_A}", [OU_A])
    assert dn_in_scopes(f"ou=3emeA-2,{OU_A}", [OU_A])
    assert dn_in_scopes("cn=3emeA,ou=groupes,dc=lycee,dc=local", ["OU=groupes,DC=lycee,DC=local"])


def test_dn_in_scopes_is_case_and_spacing_insensitive():
    assert dn_in_scopes(f"cn=thomas martin, {OU_A}", [OU_A])
    assert dn_in_scopes(
        "cn=ThomasMartin,ou=3emea,ou=eleves,dc=lycee,dc=local",
        ["OU = 3emeA , OU = eleves , DC=lycee, DC=local"],
    )


def test_dn_in_scopes_rejects_siblings_and_empty_values():
    assert not dn_in_scopes(OU_B, [OU_A])
    assert not dn_in_scopes(f"cn=thomas.martin,{OU_B}", [OU_A])
    assert not dn_in_scopes("", [OU_A])
    assert not dn_in_scopes(OU_A, [])
    assert not dn_in_scopes(OU_A, [""])


def test_scoped_role_writes_only_inside_its_delegated_scopes():
    policy = RBACPolicy("alice", role=Role.ADMIN_CLASSE, ous=[OU_A], groupes=[GROUP_A])
    assert policy.may("create_ou", f"ou=4emeB,{OU_A}")
    assert policy.may("create_group", GROUP_A)
    assert not policy.may("create_ou", OU_B)
    assert not policy.may("create_ou", f"cn=thomas.martin,{OU_B}")
    # Toutes les cibles doivent être couvertes, pas seulement la première.
    assert not policy.may("move_user", f"cn=thomas.martin,{OU_A}", OU_B)


def test_reads_are_never_scoped():
    policy = RBACPolicy("alice", role=Role.ADMIN_CLASSE, ous=[OU_A])
    assert policy.may("read_user", OU_B)
    assert policy.may("export_data", OU_B)
    assert policy.may("read_audit")


def test_scoped_role_without_any_scope_cannot_write():
    policy = RBACPolicy("alice", role=Role.HELPDESK, ous=[], groupes=[])
    assert not policy.may("reset_password", f"cn=Existing User,{OU_A}")
    assert not policy.may("disable_account", OU_A)


def test_unscoped_role_ignores_the_scopes_entirely():
    policy = RBACPolicy("alice", role=Role.ADMIN_SITE, ous=[], groupes=[])
    assert policy.may("delete_ou", OU_B)
    assert policy.may("move_user", f"cn=x,{OU_B}", OU_A)


# -- Décisions ---------------------------------------------------------------


def test_check_raises_with_an_actionable_message():
    policy = RBACPolicy("bob", role=Role.LECTURE_SEULE)
    with pytest.raises(ADInsufficientRightsError) as exc:
        policy.check("delete_user", OU_A)
    message = str(exc.value)
    assert "Supprimer définitivement un compte" in message
    assert "Lecture seule" in message
    assert "bob" in message


def test_check_refuses_an_undeclared_permission_even_for_super_admin():
    policy = RBACPolicy("admin", role=Role.SUPER_ADMIN)
    with pytest.raises(ADInsufficientRightsError) as exc:
        policy.check("une_permission_inventee")
    assert "verrouillage" in str(exc.value)


def test_check_refuses_a_target_outside_the_delegated_scopes():
    policy = RBACPolicy("alice", role=Role.ADMIN_CLASSE, ous=[OU_A])
    with pytest.raises(ADInsufficientRightsError) as exc:
        policy.check("create_ou", OU_B)
    assert "hors des OU" in str(exc.value)
    assert OU_A in str(exc.value)


def test_check_passes_when_role_and_scopes_allow_the_action():
    policy = RBACPolicy("alice", role=Role.ADMIN_CLASSE, ous=[OU_A])
    policy.check("create_ou", f"ou=4emeB,{OU_A}")  # ne lève pas
    policy.check("read_user", OU_B)  # la lecture passe toujours


def test_has_any_and_describe_summarize_the_session():
    policy = RBACPolicy("alice", role=Role.HELPDESK, ous=[OU_A], groupes=[GROUP_A])
    assert policy.has_any(["delete_user", "reset_password"]) is True
    assert policy.has_any(["delete_user", "create_user"]) is False
    described = policy.describe()
    assert "Helpdesk" in described
    assert "1 OU, 1 groupe(s)" in described
    assert RBACPolicy("bob", role=Role.LECTURE_SEULE).readonly is True


# -- Persistance (delegations.json) -----------------------------------------


def test_delegations_round_trip_in_clear_json(tmp_path):
    path = tmp_path / "delegations.json"
    grants = [
        _grant(id=unique_grant_id(), role=Role.ADMIN_CLASSE, ous=[OU_A], groupes=[GROUP_A]),
        _grant(id=unique_grant_id(), operateur="bob", role=Role.HELPDESK, site="b.local"),
    ]
    save_grants(grants, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["grants"][0]["operateur"] == "alice"  # ni chiffré ni masqué
    assert load_grants(path) == grants


def test_missing_or_corrupt_delegations_file_yields_no_grant(tmp_path):
    assert load_grants(tmp_path / "absent.json") == []
    broken = tmp_path / "delegations.json"
    broken.write_text("{ pas du json", encoding="utf-8")
    assert load_grants(broken) == []
    broken.write_text('{"grants": "pas une liste"}', encoding="utf-8")
    assert load_grants(broken) == []


def test_unknown_role_in_file_falls_back_to_read_only(tmp_path):
    path = tmp_path / "delegations.json"
    path.write_text(
        json.dumps({"version": 1, "grants": [{"id": "g1", "operateur": "alice", "role": "roi"}]}),
        encoding="utf-8",
    )
    grants = load_grants(path)
    assert grants[0].role is Role.LECTURE_SEULE


def test_build_policy_reads_the_configured_file(tmp_path, monkeypatch):
    path = tmp_path / "delegations.json"
    save_grants([_grant(role=Role.ADMIN_SITE)], path)
    monkeypatch.setattr("edusync_ad.core.rbac.delegations_path", lambda: path)
    assert build_policy("alice", "lycee.local").role is Role.ADMIN_SITE
    assert build_policy("bob", "lycee.local").role is Role.LECTURE_SEULE


def test_find_grant_and_unique_ids():
    grants = [_grant(id="g1"), _grant(id="g2")]
    assert find_grant(grants, "g2").operateur == "alice"
    assert find_grant(grants, None) is None
    assert find_grant(grants, "inconnu") is None
    generated = {unique_grant_id(grants) for _ in range(50)}
    assert generated.isdisjoint({"g1", "g2"})  # jamais une id déjà prise
    assert unique_grant_id([])


def test_validate_explains_what_is_missing():
    assert _grant(operateur="   ").validate() == [
        "Le compte de l'opérateur est obligatoire."
    ]
    errors = _grant(role=Role.ADMIN_CLASSE, ous=[], groupes=[]).validate()
    assert any("au moins une portée" in error for error in errors)
    errors = _grant(ous=["pas-un-dn"]).validate()
    assert any("DN attendu" in error for error in errors)
    assert _grant(role=Role.ADMIN_SITE, ous=[OU_A]).validate() == []


# -- Garde-fou de la connexion AD -------------------------------------------


@pytest.fixture
def ad() -> ADConnection:
    connection = ADConnection(connection_factory=make_mock_connection_factory())
    connection.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return connection


def test_writes_work_when_no_policy_is_attached(ad):
    """Hors UI (tests, scripts) ``rbac is None`` : aucun contrôle appliqué."""
    ad.create_ou(f"ou=sans-rbac,{BASE_DN}", "sans-rbac")
    assert ad.ou_exists(f"ou=sans-rbac,{BASE_DN}") is True


def test_read_only_role_cannot_write_but_can_still_read(ad):
    ad.rbac = RBACPolicy("bob", role=Role.LECTURE_SEULE)
    new_ou = f"ou=refuse,{BASE_DN}"
    with pytest.raises(ADInsufficientRightsError):
        ad.create_ou(new_ou, "refuse")
    assert ad.ou_exists(new_ou) is False  # rien n'a été écrit
    assert ad.ou_exists(OU_3EMEA_DN) is True
    assert "existing.user" in ad.search_existing_identifiers(OU_3EMEA_DN)


def test_admin_site_writes_everywhere(ad):
    ad.rbac = RBACPolicy("alice", role=Role.ADMIN_SITE)
    ad.create_ou(f"ou=libre,{BASE_DN}", "libre")  # ne lève pas
    assert ad.ou_exists(f"ou=libre,{BASE_DN}") is True


def test_scoped_admin_is_confined_to_its_delegated_ou(ad):
    ad.rbac = RBACPolicy("alice", role=Role.ADMIN_CLASSE, ous=[OU_3EMEA_DN])
    outside = f"ou=9emeX,ou=eleves,{BASE_DN}"
    with pytest.raises(ADInsufficientRightsError):
        ad.create_ou(outside, "9emeX")
    assert ad.ou_exists(outside) is False

    inside = f"ou=3emeA-2,{OU_3EMEA_DN}"
    ad.create_ou(inside, "3emeA-2")  # dans le périmètre : autorisé
    assert ad.ou_exists(inside) is True


def test_helpdesk_can_reset_password_in_scope_only(ad):
    target = f"cn=Existing User,{OU_3EMEA_DN}"
    ad.rbac = RBACPolicy("alice", role=Role.HELPDESK, ous=[OU_3EMEA_DN])
    ad.set_password(target, "NouveauMotDePasse1!")  # dans la portée : autorisé

    outside_ou = f"ou=3emeZ,ou=eleves,{BASE_DN}"
    ad.rbac = RBACPolicy("alice", role=Role.HELPDESK, ous=[f"ou=autre,{BASE_DN}"])
    with pytest.raises(ADInsufficientRightsError):
        ad.set_password(target, "EncoreUnMotDePasse2!")
    # Rôle scopé sans portée : aucune écriture, y compris sur la bonne OU.
    ad.rbac = RBACPolicy("alice", role=Role.HELPDESK, ous=[])
    with pytest.raises(ADInsufficientRightsError):
        ad.create_ou(outside_ou, "3emeZ")
    assert ad.ou_exists(outside_ou) is False


def test_group_management_targets_the_group_dn(ad):
    """``add_user_to_group`` porte sa vérification sur le groupe (``group_dn``),
    pas sur l'utilisateur : c'est le groupe qui doit être délégué."""
    user_dn = f"cn=Existing User,{OU_3EMEA_DN}"
    ad.rbac = RBACPolicy(
        "alice", role=Role.ADMIN_CLASSE, groupes=[f"ou=groupes,{BASE_DN}"]
    )
    group_dn = f"cn=3emeA,ou=groupes,{BASE_DN}"
    ad.create_group(group_dn, "3emeA")  # sous-arbre du DN de groupe délégué
    ad.add_user_to_group(user_dn, group_dn)
    ad.remove_user_from_group(user_dn, group_dn)

    # Le même opérateur ne touche pas à un groupe hors portée.
    with pytest.raises(ADInsufficientRightsError):
        ad.add_user_to_group(user_dn, f"cn=Autre,ou=autres,{BASE_DN}")


def test_action_without_declared_permission_is_refused_even_for_super_admin():
    """Verrouillage : une écriture non annotée ne passe pas le garde-fou."""

    def sample(self, dn):
        return dn

    policy = RBACPolicy("admin", role=Role.SUPER_ADMIN)
    with pytest.raises(ADInsufficientRightsError) as exc:
        _enforce_rbac(policy, sample, "Action mystère", None, ("dn",), (None, "cn=x"), {})
    assert "verrouillage" in str(exc.value)


# -- Journal d'audit par opérateur (M7 enrichi) -----------------------------


def test_journal_filters_by_operator(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()
    log.current_user = "alice"
    log.record("creation_compte", "thomas.martin", "succes", session)
    log.current_user = "bob"
    log.record("reinitialisation_mdp", "lea.dupont", "succes", session)

    assert [entry.compte for entry in log.query(utilisateur="alice")] == ["thomas.martin"]
    assert [entry.compte for entry in log.query(utilisateur="bob")] == ["lea.dupont"]
    assert log.query(utilisateur="charlie") == []
    assert log.operators() == ["alice", "bob"]


def test_journal_filters_by_operator_and_domain_together(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()
    log.current_user, log.current_domain = "alice", "lycee.local"
    log.record("creation_compte", "a", "succes", session)
    log.current_domain = "college-b.local"
    log.record("creation_compte", "b", "succes", session)
    log.current_user = "bob"
    log.record("creation_compte", "c", "succes", session)

    assert [
        entry.compte for entry in log.query(utilisateur="alice", domaine="lycee.local")
    ] == ["a"]
    assert len(log.query(utilisateur="alice")) == 2
    assert sorted(log.operators()) == ["alice", "bob"]
