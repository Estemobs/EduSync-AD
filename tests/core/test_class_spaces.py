"""Tests du module Espaces partagés par classe (M18)."""

from __future__ import annotations

from edusync_ad.core.class_spaces import (
    RIGHTS_STUDENTS,
    RIGHTS_TEACHERS,
    ClassSpaceConfig,
    ClassSpaceManager,
    ClassSpacePlan,
    classify_member,
    creation_script,
    load_class_space_config,
    load_class_space_state,
    mark_synchronized,
    notify_group_created,
    plan_sync,
    save_class_space_config,
    sync_script,
)

GROUP_DN = "CN=6emeA,OU=Classes,DC=lycee,DC=local"
TEACHERS_GROUP = "CN=Profs,DC=lycee,DC=local"
TEACHERS_OU = "OU=Profs,DC=lycee,DC=local"
OU_ELEVE = "CN=Alice Martin,OU=Classes,DC=lycee,DC=local"
OU_PROF = "CN=Jean Durand,OU=Profs,DC=lycee,DC=local"


class TestConfig:
    def test_default_path(self):
        cfg = ClassSpaceConfig()
        assert cfg.resolve_path("6emeA") == r"\\srv\classes\6emeA"

    def test_custom_root_and_template(self):
        cfg = ClassSpaceConfig(share_root=r"\\fs01\partages\\", folder_template=r"Classes\%GROUP%")
        assert cfg.resolve_path("6emeB") == r"\\fs01\partages\Classes\6emeB"

    def test_valid_default(self):
        assert ClassSpaceConfig().validate() == []

    def test_empty_root(self):
        assert ClassSpaceConfig(share_root=" ").validate()

    def test_non_unc_root(self):
        assert any("UNC" in e for e in ClassSpaceConfig(share_root="D:/classes").validate())

    def test_template_without_group_placeholder(self):
        assert any("%GROUP%" in e for e in ClassSpaceConfig(folder_template="classes").validate())

    def test_dict_round_trip(self):
        cfg = ClassSpaceConfig(
            share_root=r"\\fs02\cls", folder_template="%GROUP%-2026",
            teachers_group=TEACHERS_GROUP, teachers_ou=TEACHERS_OU,
            auto_on_group_create=False,
        )
        restored = ClassSpaceConfig.from_dict(cfg.to_dict())
        assert restored == cfg


class TestClassification:
    def test_member_of_teachers_group_is_teacher(self):
        cfg = ClassSpaceConfig(teachers_group=TEACHERS_GROUP)
        assert classify_member(OU_PROF, [TEACHERS_GROUP], cfg) == "prof"

    def test_member_under_teachers_ou_is_teacher(self):
        cfg = ClassSpaceConfig(teachers_ou=TEACHERS_OU)
        assert classify_member(OU_PROF, [], cfg) == "prof"

    def test_case_insensitive_ou_match(self):
        cfg = ClassSpaceConfig(teachers_ou=TEACHERS_OU.upper())
        assert classify_member(OU_PROF.lower(), [], cfg) == "prof"

    def test_ordinary_member_is_student(self):
        cfg = ClassSpaceConfig(teachers_ou=TEACHERS_OU, teachers_group=TEACHERS_GROUP)
        assert classify_member(OU_ELEVE, [], cfg) == "eleve"

    def test_no_rule_defaults_to_student(self):
        assert classify_member(OU_ELEVE, [], ClassSpaceConfig()) == "eleve"


class TestManager:
    def test_plan_path_and_roles(self):
        cfg = ClassSpaceConfig(teachers_ou=TEACHERS_OU)
        mgr = ClassSpaceManager(cfg, connection=None)
        plan = mgr.plan_for_group(GROUP_DN, "6emeA", [
            {"dn": OU_ELEVE, "sam": "amartin"},
            {"dn": OU_PROF, "sam": "jdurand"},
        ])
        assert plan.path == r"\\srv\classes\6emeA"
        assert plan.share_name == "6emeA"
        assert plan.teachers == ["jdurand"]
        assert plan.students == ["amartin"]
        assert plan.members == ["jdurand", "amartin"]

    def test_plan_with_string_members(self):
        mgr = ClassSpaceManager(ClassSpaceConfig(), connection=None)
        plan = mgr.plan_for_group(GROUP_DN, "6emeA", [OU_ELEVE])
        assert plan.students == [OU_ELEVE]

    def test_plan_without_members(self):
        mgr = ClassSpaceManager(ClassSpaceConfig(), connection=None)
        plan = mgr.plan_for_group(GROUP_DN, "6emeA")
        assert plan.teachers == [] and plan.students == []
        assert plan.path.endswith("6emeA")


class TestCreationScript:
    def _plan(self) -> ClassSpacePlan:
        return ClassSpacePlan(
            group_dn=GROUP_DN,
            group_name="6emeA",
            path=r"\\srv\classes\6emeA",
            teachers=["jdurand"],
            students=["amartin", "bbernard"],
        )

    def test_contains_smb_share_and_folder(self):
        script = creation_script([self._plan()], domain="lycee.local")
        assert "New-Item" in script
        assert "New-SmbShare" in script
        assert "-Name '6emeA'" in script
        assert r"\\srv\classes\6emeA" in script

    def test_contains_role_rights(self):
        script = creation_script([self._plan()], domain="lycee.local")
        assert rf"lycee.local\jdurand:(OI)(CI){RIGHTS_TEACHERS}" in script
        assert rf"lycee.local\amartin:(OI)(CI){RIGHTS_STUDENTS}" in script
        assert "BUILTIN\\Administrators:(OI)(CI)FullControl" in script
        assert "NT AUTHORITY\\SYSTEM:(OI)(CI)FullControl" in script

    def test_multiple_plans(self):
        plans = [
            self._plan(),
            ClassSpacePlan(GROUP_DN, "5emeB", r"\\srv\classes\5emeB"),
        ]
        script = creation_script(plans, domain="lycee.local")
        assert script.count("New-SmbShare") == 2
        assert "5emeB" in script

    def test_script_is_powershell(self):
        script = creation_script([self._plan()])
        assert script.startswith("#Requires -RunAsAdministrator")


class TestSyncScript:
    def _plan(self) -> ClassSpacePlan:
        return ClassSpacePlan(GROUP_DN, "6emeA", r"\\srv\classes\6emeA")

    def test_queries_ad_membership(self):
        script = sync_script(self._plan(), domain="lycee.local")
        assert "Import-Module ActiveDirectory" in script
        assert "Get-ADGroupMember" in script
        assert GROUP_DN in script

    def test_classifies_and_rebuilds_rights(self):
        script = sync_script(self._plan(), domain="lycee.local")
        assert "Modify" in script and "ReadAndExecute" in script
        assert "Get-Acl" in script and "Set-Acl" in script
        assert "RemoveAccessRule" in script
        assert "AddAccessRule" in script

    def test_protects_admin_sids(self):
        script = sync_script(self._plan())
        assert "S-1-5-32-544" in script   # Administrateurs
        assert "S-1-5-18" in script       # SYSTEM
        assert "IsInherited" in script    # ACE hérités intacts

    def test_teachers_group_injected_when_configured(self):
        script = sync_script(
            self._plan(), teachers_group=TEACHERS_GROUP, teachers_ou=TEACHERS_OU
        )
        assert TEACHERS_GROUP in script
        assert TEACHERS_OU.lower() in script

    def test_no_teachers_rule_when_unconfigured(self):
        script = sync_script(self._plan())
        assert "$teachersGroup = $null" in script
        assert "$teachersOU = $null" in script


class TestPlanSync:
    def test_added_and_removed(self):
        diff = plan_sync(["a", "b", "c"], ["b", "c", "d"])
        assert diff["added"] == ["d"]
        assert diff["removed"] == ["a"]
        assert diff["unchanged"] == ["b", "c"]

    def test_identical(self):
        diff = plan_sync(["a"], ["a"])
        assert diff["added"] == [] and diff["removed"] == []

    def test_first_sync(self):
        diff = plan_sync([], ["x", "y"])
        assert diff["added"] == ["x", "y"] and diff["removed"] == []


class TestPersistence:
    def test_config_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.class_spaces as mod
        monkeypatch.setattr(mod, "CLASS_SPACE_CONFIG_FILE", tmp_path / "cfg.json")
        cfg = ClassSpaceConfig(share_root=r"\\fs\cls", auto_on_group_create=False)
        save_class_space_config(cfg)
        assert load_class_space_config() == cfg

    def test_config_missing_file(self, tmp_path, monkeypatch):
        import edusync_ad.core.class_spaces as mod
        monkeypatch.setattr(mod, "CLASS_SPACE_CONFIG_FILE", tmp_path / "absent.json")
        assert load_class_space_config() == ClassSpaceConfig()

    def test_config_corrupted_file(self, tmp_path, monkeypatch):
        import edusync_ad.core.class_spaces as mod
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "CLASS_SPACE_CONFIG_FILE", bad)
        assert load_class_space_config() == ClassSpaceConfig()

    def test_state_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.class_spaces as mod
        monkeypatch.setattr(mod, "CLASS_SPACE_STATE_FILE", tmp_path / "state.json")
        mark_synchronized(GROUP_DN, ["b", "a"])
        state = load_class_space_state()
        assert state[GROUP_DN]["members"] == ["a", "b"]
        assert state[GROUP_DN]["pending"] is False


class TestNotifyGroupCreated:
    def _patch(self, tmp_path, monkeypatch, auto: bool):
        import edusync_ad.core.class_spaces as mod
        monkeypatch.setattr(mod, "CLASS_SPACE_CONFIG_FILE", tmp_path / "cfg.json")
        monkeypatch.setattr(mod, "CLASS_SPACE_STATE_FILE", tmp_path / "state.json")
        save_class_space_config(ClassSpaceConfig(auto_on_group_create=auto))

    def test_disabled_returns_none(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch, auto=False)
        assert notify_group_created(GROUP_DN, "6emeA") is None
        assert load_class_space_state() == {}

    def test_enabled_registers_pending_space(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch, auto=True)
        path = notify_group_created(GROUP_DN, "6emeA")
        assert path == r"\\srv\classes\6emeA"
        state = load_class_space_state()
        entry = state[GROUP_DN]
        assert entry["pending"] is True
        assert entry["path"] == path
        assert entry["group_name"] == "6emeA"

    def test_group_name_extracted_from_dn(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch, auto=True)
        path = notify_group_created("CN=5emeB,OU=Classes,DC=lycee,DC=local")
        assert path == r"\\srv\classes\5emeB"

    def test_notify_is_idempotent(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch, auto=True)
        notify_group_created(GROUP_DN, "6emeA")
        notify_group_created(GROUP_DN, "6emeA")
        assert len(load_class_space_state()) == 1
