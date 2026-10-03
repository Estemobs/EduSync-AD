"""Tests du module de profils utilisateurs (M13)."""

from __future__ import annotations

import pytest

from edusync_ad.core.profiles import (
    ProfileConfig,
    ProfileManager,
    ProfileTemplate,
    ProfileType,
    dict_to_profile_config,
    load_all_profile_configs,
    profile_config_to_dict,
    save_all_profile_configs,
)

OU_ELEVE = "OU=6emeA,OU=Eleves,DC=lycee,DC=local"
OU_ENSEIGNANTS = "OU=Profs,DC=lycee,DC=local"
GROUP_PROFS = "CN=Profs,DC=lycee,DC=local"


class TestProfileConfig:
    def test_roaming_path_resolution(self):
        cfg = ProfileConfig(
            profile_type=ProfileType.ROAMING,
            roaming_path=r"\\srv\profil\%USERNAME%",
        )
        path = cfg.get_profile_path("jean.dupont", "jdupont", OU_ELEVE, "lycee.local")
        assert path == r"\\srv\profil\jean.dupont"

    def test_all_variables_supported(self):
        cfg = ProfileConfig(
            profile_type=ProfileType.ROAMING,
            roaming_path=r"\\%DOMAIN%\%OU%\%SAM%\%USERNAME%",
        )
        path = cfg.get_profile_path("Jean Dupont", "jdupont", OU_ELEVE, "lycee.local")
        assert path == r"\\lycee.local\6emeA\jdupont\Jean Dupont"

    def test_mandatory_path_uses_ou_name(self):
        cfg = ProfileConfig(
            profile_type=ProfileType.MANDATORY,
            mandatory_path=r"\\srv\%OU%.man",
        )
        path = cfg.get_profile_path("jdupont", "jdupont", OU_ELEVE, "lycee.local")
        assert path == r"\\srv\6emeA.man"

    def test_local_without_path_returns_empty(self):
        cfg = ProfileConfig(profile_type=ProfileType.LOCAL)
        assert cfg.get_profile_path("u", "s", OU_ELEVE, "lycee.local") == ""

    def test_empty_template_returns_empty(self):
        cfg = ProfileConfig(profile_type=ProfileType.ROAMING, roaming_path="")
        assert cfg.get_profile_path("u", "s", OU_ELEVE, "lycee.local") == ""

    def test_ou_name_taken_from_deepest_ou_only(self):
        # %OU% doit être le nom de l'OU la plus proche (6emeA), pas un parent
        cfg = ProfileConfig(profile_type=ProfileType.MANDATORY, mandatory_path=r"%OU%")
        path = cfg.get_profile_path("u", "s", "OU=Classe,OU=College,DC=x,DC=local", "x.local")
        assert path == "Classe"


class TestProfileTemplates:
    def test_builtin_templates_exist(self):
        templates = ProfileTemplate.builtin_templates()
        assert len(templates) >= 4
        names = [t.name for t in templates]
        assert any("itinérant" in n for n in names)
        assert any("obligatoire" in n for n in names)

    def test_builtin_templates_have_valid_config(self):
        for template in ProfileTemplate.builtin_templates():
            assert isinstance(template.config, ProfileConfig)
            assert template.description
            if template.config.profile_type == ProfileType.ROAMING:
                assert template.config.roaming_path
            elif template.config.profile_type == ProfileType.MANDATORY:
                assert template.config.mandatory_path


class TestProfileManager:
    def _make_manager(self) -> ProfileManager:
        pm = ProfileManager(None)
        pm.set_default_config(ProfileConfig(profile_type=ProfileType.LOCAL))
        return pm

    def test_group_has_priority_over_ou(self):
        pm = self._make_manager()
        pm.set_ou_config(OU_ELEVE, ProfileConfig(profile_type=ProfileType.MANDATORY, mandatory_path=r"\s\%OU%.man"))
        pm.set_group_config(GROUP_PROFS, ProfileConfig(profile_type=ProfileType.ROAMING, roaming_path=r"\p\%SAM%"))
        cfg = pm.get_config_for_user("dn", "u", "s", OU_ELEVE, "lycee.local", [GROUP_PROFS])
        assert cfg.profile_type == ProfileType.ROAMING

    def test_ou_has_priority_over_default(self):
        pm = self._make_manager()
        pm.set_ou_config(OU_ELEVE, ProfileConfig(profile_type=ProfileType.MANDATORY, mandatory_path=r"\s\%OU%.man"))
        cfg = pm.get_config_for_user("dn", "u", "s", OU_ELEVE, "lycee.local")
        assert cfg.profile_type == ProfileType.MANDATORY

    def test_parent_ou_config_is_used_when_child_unset(self):
        pm = self._make_manager()
        pm.set_ou_config("OU=Eleves,DC=lycee,DC=local", ProfileConfig(profile_type=ProfileType.ROAMING, roaming_path=r"\p\%SAM%"))
        cfg = pm.get_config_for_user("dn", "u", "s", OU_ELEVE, "lycee.local")
        assert cfg.profile_type == ProfileType.ROAMING

    def test_default_fallback(self):
        pm = self._make_manager()
        cfg = pm.get_config_for_user("dn", "u", "s", "OU=Inconnue,DC=lycee,DC=local", "lycee.local")
        assert cfg.profile_type == ProfileType.LOCAL

    def test_group_list_without_groups_falls_back_to_ou(self):
        pm = self._make_manager()
        pm.set_ou_config(OU_ELEVE, ProfileConfig(profile_type=ProfileType.MANDATORY, mandatory_path=r"\m\%OU%.man"))
        cfg = pm.get_config_for_user("dn", "u", "s", OU_ELEVE, "lycee.local", None)
        assert cfg.profile_type == ProfileType.MANDATORY


class TestPersistence:
    def test_dict_round_trip(self):
        cfg = ProfileConfig(profile_type=ProfileType.ROAMING, roaming_path=r"\p\%USERNAME%")
        data = profile_config_to_dict(cfg)
        restored = dict_to_profile_config(data)
        assert restored.profile_type == ProfileConfig().profile_type.__class__(ProfileType.ROAMING)
        assert restored.roaming_path == cfg.roaming_path

    def test_save_load_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.profiles as profiles_mod
        monkeypatch.setattr(profiles_mod, "PROFILE_CONFIG_FILE", tmp_path / "profile_configs.json")

        ou_configs = {OU_ELEVE: ProfileConfig(profile_type=ProfileType.MANDATORY, mandatory_path=r"\m.man")}
        group_configs = {GROUP_PROFS: ProfileConfig(profile_type=ProfileType.ROAMING, roaming_path=r"\p\%SAM%")}
        default = ProfileConfig(profile_type=ProfileType.LOCAL)

        save_all_profile_configs(ou_configs, group_configs, default)
        loaded_ou, loaded_group, loaded_default = load_all_profile_configs()

        assert loaded_ou[OU_ELEVE].profile_type == ProfileType.MANDATORY
        assert loaded_group[GROUP_PROFS].roaming_path == r"\p\%SAM%"
        assert loaded_default.profile_type == ProfileType.LOCAL
