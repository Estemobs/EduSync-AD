"""Tests du module Modèles de groupes (M23)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.templates import (
    GROUP_SCOPES,
    GROUP_TYPE_VALUES,
    TEMPLATE_KINDS,
    GroupSpec,
    GroupTemplate,
    InstantiationResult,
    builtin_templates,
    duplicate_template,
    export_template,
    export_template_xml,
    group_sam,
    import_template,
    import_template_xml,
    instantiate,
    load_templates,
    render_pattern,
    save_templates,
    unique_template_id,
    validate_values,
)

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
ELEVES_DN = f"ou=Eleves,{BASE_DN}"


def make_mock_factory():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(
        ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"}
    )
    seed.strategy.add_entry(
        ELEVES_DN, {"objectClass": "organizationalUnit", "ou": "Eleves"}
    )

    def factory(controller, bind_user, password, use_ssl):
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    conn = ADConnection(connection_factory=make_mock_factory())
    conn.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return conn


@pytest.fixture
def scoped_configs(tmp_path, monkeypatch):
    """Redirige les persystances profil / quota / dossier perso vers tmp."""
    import edusync_ad.core.homedirs as homedirs_mod
    import edusync_ad.core.profiles as profiles_mod
    import edusync_ad.core.quotas as quotas_mod

    monkeypatch.setattr(profiles_mod, "PROFILE_CONFIG_FILE", tmp_path / "profile_configs.json")
    monkeypatch.setattr(quotas_mod, "QUOTA_CONFIG_FILE", tmp_path / "quota_configs.json")
    monkeypatch.setattr(homedirs_mod, "HOME_CONFIG_FILE", tmp_path / "home_dir_config.json")
    return tmp_path


def get_builtin(template_id: str) -> GroupTemplate:
    return next(t for t in builtin_templates() if t.id == template_id)


def groups_by_sam(connection, base_dn) -> dict:
    entries = connection.search_entries(
        base_dn, "(objectClass=group)", ["groupType", "sAMAccountName"]
    )
    return {e["sAMAccountName"]: e for e in entries}


class TestGroupSpec:
    def test_valid_spec(self):
        assert GroupSpec("{classe}", "global", "élèves").validate() == []

    def test_empty_name(self):
        assert any("vide" in e for e in GroupSpec("").validate())

    def test_unknown_scope(self):
        errors = GroupSpec("g", "galactique").validate()
        assert any("Portée" in e for e in errors)

    def test_bad_pattern(self):
        assert any("accolades" in e for e in GroupSpec("g-{classe").validate())
        assert any("champ" in e for e in GroupSpec("{classe.1}").validate())

    def test_round_trip(self):
        spec = GroupSpec("{classe}-Profs", "universal", "desc")
        assert GroupSpec.from_dict(spec.to_dict()) == spec

    def test_scope_catalog(self):
        assert set(GROUP_SCOPES) == {"global", "domainlocal", "universal"}
        assert set(GROUP_TYPE_VALUES) == set(GROUP_SCOPES)


class TestBuiltinTemplates:
    def test_four_templates_all_valid(self):
        templates = builtin_templates()
        assert len(templates) == 4
        for template in templates:
            assert template.validate() == [], (template.id, template.validate())

    def test_kinds_cover_the_four_roadmap_profiles(self):
        assert {t.kind for t in builtin_templates()} == {
            "classe_eleve", "classe_prof", "personnel_admin", "service_technique"
        }
        assert set(TEMPLATE_KINDS) >= {"classe_eleve", "custom"}

    def test_class_template_covers_all_roadmap_fields(self):
        """Chaque modèle définit : OU, groupes, scripts, quotas, dossier,
        partage, profil, heures, MDP, licences (M23 / ROADMAP)."""
        template = get_builtin("classe_eleve")
        assert template.ou_rdn_pattern == "Eleves-{classe}"
        assert template.groups and template.logon_script is not None
        assert template.quota is not None and template.quota.size_gb == 5.0
        assert template.home is not None and template.space is not None
        assert template.profile is not None
        assert template.hours_preset == "Heures cours"
        assert template.password_policy is not None
        assert isinstance(template.licenses, list)

    def test_placeholders(self):
        assert get_builtin("classe_eleve").placeholders() == ["classe"]
        assert get_builtin("classe_prof").placeholders() == ["classe"]
        assert get_builtin("personnel_admin").placeholders() == []
        assert get_builtin("service_technique").placeholders() == []

    def test_service_technique_has_no_hour_restriction(self):
        assert get_builtin("service_technique").hours_preset == ""

    def test_admin_group_is_universal(self):
        scopes = {g.name_pattern: g.scope for g in get_builtin("personnel_admin").groups}
        assert scopes["Administrateurs"] == "universal"
        assert scopes["Personnel-Admin"] == "global"


class TestValidation:
    def test_bad_id(self):
        template = get_builtin("classe_eleve").clone()
        template.id = "Mauvais ID"
        assert any("Identifiant" in e for e in template.validate())

    def test_empty_name(self):
        template = get_builtin("classe_eleve").clone()
        template.name = " "
        assert any("vide" in e for e in template.validate())

    def test_unknown_kind(self):
        template = get_builtin("classe_eleve").clone()
        template.kind = "alien"
        assert any("inconnu" in e for e in template.validate())

    def test_no_groups(self):
        template = get_builtin("classe_eleve").clone()
        template.groups = []
        assert any("Aucun groupe" in e for e in template.validate())

    def test_duplicate_group_pattern(self):
        template = get_builtin("classe_eleve").clone()
        template.groups.append(GroupSpec("{classe}"))
        assert any("dupliqué" in e for e in template.validate())

    def test_unknown_hours_preset(self):
        template = get_builtin("classe_eleve").clone()
        template.hours_preset = "Heures folles"
        assert any("Préréglage" in e for e in template.validate())

    def test_password_length_bounds(self):
        template = get_builtin("classe_eleve").clone()
        template.password_policy.longueur = 3
        assert any("6 à 128" in e for e in template.validate())

    def test_empty_license(self):
        template = get_builtin("classe_eleve").clone()
        template.licenses = ["sku-ok", "  "]
        assert any("Licence" in e for e in template.validate())

    def test_invalid_script(self):
        template = get_builtin("classe_eleve").clone()
        template.logon_script.content = ""
        assert any("script" in e.lower() for e in template.validate())

    def test_empty_parent_is_allowed_in_definition(self):
        """L'OU parente peut être choisie à l'instanciation (builtins)."""
        assert get_builtin("classe_eleve").ou_parent_dn == ""
        assert get_builtin("classe_eleve").validate() == []

    def test_quota_and_home_sub_validations(self):
        template = get_builtin("classe_eleve").clone()
        template.quota.size_gb = 0
        errors = template.validate()
        assert any("strictement positive" in e for e in errors)


class TestPatterns:
    def test_render(self):
        assert render_pattern("Eleves-{classe}", {"classe": "3emeA"}) == "Eleves-3emeA"
        assert render_pattern("{classe}-Profs", {"classe": "6B"}) == "6B-Profs"

    def test_render_missing_value(self):
        with pytest.raises(ValueError, match="inconnu"):
            render_pattern("{inconnu}", {})

    def test_validate_values(self):
        template = get_builtin("classe_eleve")
        assert validate_values(template, {"classe": "3emeA"}) == []
        assert any("classe" in e for e in validate_values(template, {}))
        assert any("classe" in e for e in validate_values(template, {"classe": " "}))

    def test_group_sam(self):
        assert group_sam("6B-Profs") == "6B-Profs"
        assert group_sam('a/b:c*d?"e') == "a-b-c-d--e"
        assert group_sam("...") == "groupe"


class TestPersistence:
    def test_missing_file_returns_builtins(self, tmp_path, monkeypatch):
        import edusync_ad.core.templates as mod

        monkeypatch.setattr(mod, "TEMPLATES_FILE", tmp_path / "absent.json")
        assert [t.id for t in load_templates()] == [t.id for t in builtin_templates()]

    def test_save_load_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.templates as mod

        monkeypatch.setattr(mod, "TEMPLATES_FILE", tmp_path / "t.json")
        templates = builtin_templates()
        save_templates(templates)
        assert [t.id for t in load_templates()] == [t.id for t in templates]
        assert mod.TEMPLATES_FILE.exists()

    def test_corrupted_file_returns_builtins(self, tmp_path, monkeypatch):
        import edusync_ad.core.templates as mod

        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "TEMPLATES_FILE", bad)
        assert len(load_templates()) == 4


class TestDuplicate:
    def test_unique_id_appends_counter(self):
        templates = builtin_templates()
        assert unique_template_id(templates, "classe_eleve") == "classe_eleve-2"

    def test_unique_id_normalizes(self):
        templates = builtin_templates()
        assert unique_template_id(templates, "Nouveau Modèle!") == "nouveau-modele"
        assert unique_template_id(templates, "!!!") == "modele"

    def test_duplicate_creates_copy(self):
        templates = builtin_templates()
        source = templates[0]
        clone = duplicate_template(templates, source, "")
        assert clone.id == "classe_eleve-2"
        assert clone.name == "Classe élève (copie)"
        assert clone.to_dict() != source.to_dict()
        assert len(templates) == 4  # liste d'origine intacte

    def test_duplicate_with_name(self):
        clone = duplicate_template(builtin_templates(), get_builtin("classe_eleve"), "Classe 6e")
        assert clone.name == "Classe 6e"


class TestJsonXml:
    def test_dict_round_trip(self):
        template = get_builtin("classe_eleve")
        assert GroupTemplate.from_dict(template.to_dict()).to_dict() == template.to_dict()

    def test_clone_is_deep(self):
        template = get_builtin("classe_eleve")
        clone = template.clone()
        clone.groups[0].name_pattern = "autre"
        assert template.groups[0].name_pattern == "{classe}"

    def test_json_export_import(self, tmp_path):
        template = get_builtin("classe_eleve")
        path = export_template(template, tmp_path / "m.json")
        assert import_template(path).to_dict() == template.to_dict()

    def test_xml_export_import(self, tmp_path):
        """Aller-retour XML strictement équivalent au JSON."""
        template = get_builtin("classe_eleve")
        path = export_template_xml(template, tmp_path / "m.xml")
        assert import_template_xml(path).to_dict() == template.to_dict()

    def test_xml_preserves_crlf_and_nulls(self, tmp_path):
        template = get_builtin("classe_eleve")
        path = export_template_xml(template, tmp_path / "m.xml")
        data = import_template_xml(path).to_dict()
        assert "\r\n" in data["logon_script"]["content"]
        assert data["password_policy"]["pattern_fixe"] is None
        assert data["quota"]["hard_limit"] is False
        assert data["licenses"] == []

    def test_import_corrupted_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        with pytest.raises(ValueError, match="impossible"):
            import_template(bad)

    def test_import_json_array_rejected(self, tmp_path):
        arr = tmp_path / "arr.json"
        arr.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(ValueError):
            import_template(arr)

    def test_import_invalid_template_rejected(self, tmp_path):
        path = tmp_path / "inval.json"
        path.write_text(
            json.dumps(
                {"id": "ok", "name": "", "groups": [{"name_pattern": "g", "scope": "global"}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Modèle invalide"):
            import_template(path)

    def test_import_xml_wrong_root(self, tmp_path):
        bad = tmp_path / "bad.xml"
        bad.write_text("<autre/>", encoding="utf-8")
        with pytest.raises(ValueError, match="Racine"):
            import_template_xml(bad)

    def test_import_xml_broken(self, tmp_path):
        broken = tmp_path / "broken.xml"
        broken.write_text("<modele", encoding="utf-8")
        with pytest.raises(ValueError):
            import_template_xml(broken)


class TestInstantiate:
    def test_full_flow(self, ad, scoped_configs):
        template = get_builtin("classe_eleve")
        artifacts = scoped_configs / "artefacts"
        result = instantiate(
            ad, template, {"classe": "3emeA"},
            ou_parent_dn=ELEVES_DN, artifacts_dir=artifacts,
        )

        # OU + groupe créés
        assert result.ou_created
        assert result.ou_dn == f"ou=Eleves-3emeA,{ELEVES_DN}"
        assert [g.name for g in result.groups] == ["3emeA"]
        assert result.groups[0].dn == f"cn=3emeA,{result.ou_dn}"
        assert result.groups[0].sam == "3emeA" and result.groups[0].created
        assert any("Eleves-3emeA" in child for child in ad.list_ou_children(ELEVES_DN))

        # configs scopées persistées (M13 profil / M14 quota)
        assert any("profil" in item for item in result.registered)
        assert any("quota" in item for item in result.registered)
        import edusync_ad.core.profiles as profiles_mod
        import edusync_ad.core.quotas as quotas_mod

        ou_cfg, _, _ = profiles_mod.load_all_profile_configs()
        assert result.ou_dn in ou_cfg
        ou_q, _, _ = quotas_mod.load_all_quota_configs()
        assert result.ou_dn in ou_q and ou_q[result.ou_dn].size_gb == 5.0

    def test_group_created_with_global_type(self, ad, scoped_configs):
        template = get_builtin("classe_eleve")
        result = instantiate(ad, template, {"classe": "3emeA"}, ou_parent_dn=ELEVES_DN)
        found = groups_by_sam(ad, result.ou_dn)
        assert int(found["3emeA"]["groupType"]) == GROUP_TYPE_VALUES["global"]

    def test_idempotent_second_run(self, ad, scoped_configs):
        template = get_builtin("classe_eleve")
        first = instantiate(ad, template, {"classe": "3emeA"}, ou_parent_dn=ELEVES_DN)
        second = instantiate(ad, template, {"classe": "3emeA"}, ou_parent_dn=ELEVES_DN)
        assert first.ou_created and not second.ou_created
        assert first.groups[0].created and not second.groups[0].created
        assert len(ad.list_ou_children(ELEVES_DN)) == 1

    def test_universal_and_global_scopes_written(self, ad, scoped_configs):
        template = get_builtin("personnel_admin")
        result = instantiate(ad, template, {}, ou_parent_dn=ELEVES_DN)
        found = groups_by_sam(ad, result.ou_dn)
        assert int(found["Administrateurs"]["groupType"]) == GROUP_TYPE_VALUES["universal"]
        assert int(found["Personnel-Admin"]["groupType"]) == GROUP_TYPE_VALUES["global"]

    def test_parent_override(self, ad, scoped_configs):
        template = get_builtin("service_technique")
        result = instantiate(ad, template, {}, ou_parent_dn=ELEVES_DN)
        assert result.ou_dn == f"ou=Services-Techniques,{ELEVES_DN}"

    def test_without_dedicated_ou_groups_go_to_parent(self, ad, scoped_configs):
        template = get_builtin("personnel_admin").clone()
        template.ou_rdn_pattern = ""
        result = instantiate(ad, template, {}, ou_parent_dn=ELEVES_DN)
        assert result.ou_dn == "" and not result.ou_created
        assert all(g.dn.endswith(f",{ELEVES_DN}") for g in result.groups)

    def test_apply_home_globally_option(self, ad, scoped_configs):
        import edusync_ad.core.homedirs as homedirs_mod

        template = get_builtin("service_technique")
        # par défaut : rien n'écrase la config globale
        instantiate(ad, template, {}, ou_parent_dn=ELEVES_DN)
        assert not homedirs_mod.HOME_CONFIG_FILE.exists()

        result = instantiate(
            ad, template, {}, ou_parent_dn=ELEVES_DN, apply_home_globally=True
        )
        assert any("globale" in item for item in result.registered)
        assert homedirs_mod.load_home_dir_config().share_root == r"\\srv\homes"

    def test_artifacts_generated(self, ad, scoped_configs):
        template = get_builtin("classe_eleve")
        artifacts = scoped_configs / "arts"
        result = instantiate(
            ad, template, {"classe": "3emeA"},
            ou_parent_dn=ELEVES_DN, artifacts_dir=artifacts,
        )
        names = sorted(p.name for p in artifacts.iterdir())
        assert names == [
            "espaces_classes.ps1", "instantiation-3emeA.json",
            "mapping-lecteur-h.bat", "quotas_fsr.ps1",
        ]

        # .bat sans BOM, .ps1 avec BOM (convention M15/M14/M18)
        raw = (artifacts / "mapping-lecteur-h.bat").read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.startswith(b"@echo off")

        share = (artifacts / "espaces_classes.ps1").read_text(encoding="utf-8-sig")
        assert "New-SmbShare -Name '3emeA'" in share
        assert "\\\\srv\\classes\\3emeA" in share

        quota = (artifacts / "quotas_fsr.ps1").read_text(encoding="utf-8-sig")
        assert "New-FsrmQuotaTemplate" in quota
        assert "5368709120" in quota  # 5 Go en octets

        assert result.report_path and Path(result.report_path).exists()

    def test_report_contents(self, ad, scoped_configs):
        template = get_builtin("classe_eleve")
        template.licenses = ["SkuA3-student"]
        artifacts = scoped_configs / "arts2"
        result = instantiate(
            ad, template, {"classe": "6emeB"},
            ou_parent_dn=ELEVES_DN, artifacts_dir=artifacts,
        )
        report = json.loads(
            (artifacts / "instantiation-6emeB.json").read_text(encoding="utf-8")
        )
        assert report["valeurs"] == {"classe": "6emeB"}
        assert report["ou_parente"] == ELEVES_DN
        assert report["groupes"][0]["scope"] == "global"
        assert report["heures_connexion"]["preset"] == "Heures cours"
        assert report["heures_connexion"]["resume"]
        assert report["politique_mdp"]["longueur"] == 12
        assert report["licences_o365"] == ["SkuA3-student"]
        assert report["dossier_personnel"]["drive_letter"] == "H:"
        assert report["horodatage"]
        assert result.report_path

    def test_no_artifacts_dir(self, ad, scoped_configs):
        result = instantiate(
            ad, get_builtin("classe_eleve"), {"classe": "X"}, ou_parent_dn=ELEVES_DN
        )
        assert result.artifacts == [] and result.report_path == ""

    def test_summary_mentions_key_facts(self, ad, scoped_configs):
        result = instantiate(
            ad, get_builtin("classe_eleve"), {"classe": "3emeA"}, ou_parent_dn=ELEVES_DN
        )
        summary = result.summary()
        assert "Classe élève" in summary
        assert "ou=Eleves-3emeA" in summary
        assert "créée" in summary and "créé(s)" in summary

    def test_missing_values_fails_before_any_ad_write(self, ad, scoped_configs):
        with pytest.raises(ValueError, match="classe"):
            instantiate(ad, get_builtin("classe_eleve"), {}, ou_parent_dn=ELEVES_DN)
        assert ad.list_ou_children(ELEVES_DN) == []

    def test_invalid_script_fails_before_any_ad_write(self, ad, scoped_configs):
        template = get_builtin("classe_eleve").clone()
        template.logon_script.content = ""
        with pytest.raises(ValueError, match="contenu du script"):
            instantiate(ad, template, {"classe": "X"}, ou_parent_dn=ELEVES_DN)
        assert ad.list_ou_children(ELEVES_DN) == []

    def test_missing_parent_rejected(self, ad, scoped_configs):
        with pytest.raises(ValueError, match="parente"):
            instantiate(ad, get_builtin("classe_eleve"), {"classe": "X"}, ou_parent_dn="")

    def test_requires_connection(self):
        plain = ADConnection(connection_factory=make_mock_factory())
        with pytest.raises(ADError):
            instantiate(
                plain, get_builtin("classe_eleve"), {"classe": "X"}, ou_parent_dn=ELEVES_DN
            )


class TestInstantiationResult:
    def test_to_dict_serializable(self):
        result = InstantiationResult(template_id="x", template_name="X")
        data = result.to_dict()
        assert data["id"] == "x" and data["groupes"] == []
        json.dumps(data)  # ne doit pas lever

    def test_summary_without_ou(self):
        result = InstantiationResult(template_id="x", template_name="X")
        assert "Modèle « X » instancié." in result.summary()
