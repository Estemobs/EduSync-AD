"""Tests du module Dossiers personnels / Home Directory (M17)."""

from __future__ import annotations

from edusync_ad.core.homedirs import (
    HomeDirConfig,
    HomeDirManager,
    HomeDirPlan,
    load_home_dir_config,
    save_home_dir_config,
)

OU_ELEVE = "OU=6emeA,OU=Eleves,DC=lycee,DC=local"
USERS = [
    {"dn": "CN=Dupont Jean,OU=6emeA,OU=Eleves,DC=lycee,DC=local",
     "sam": "jdupont", "cn": "Dupont Jean"},
    {"dn": "CN=Martin Léa,OU=6emeA,OU=Eleves,DC=lycee,DC=local",
     "sam": "lmartin", "cn": "Martin Léa"},
]


class TestHomeDirConfigResolve:
    def test_default_template_uses_username(self):
        cfg = HomeDirConfig()
        path = cfg.resolve("Dupont Jean", "jdupont", OU_ELEVE, "lycee.local")
        assert path == r"\\srv\homes\Dupont Jean"

    def test_template_with_sam(self):
        cfg = HomeDirConfig(folder_template="%SAM%")
        path = cfg.resolve("Dupont Jean", "jdupont", OU_ELEVE, "lycee.local")
        assert path == r"\\srv\homes\jdupont"

    def test_template_with_ou_and_domain(self):
        cfg = HomeDirConfig(share_root=r"\\fs01\homes", folder_template=r"%DOMAIN%\%OU%\%SAM%")
        path = cfg.resolve("u", "jdupont", OU_ELEVE, "lycee.local")
        assert path == r"\\fs01\homes\lycee.local\6emeA\jdupont"

    def test_trailing_backslash_on_share_root_is_normalized(self):
        cfg = HomeDirConfig(share_root=r"\\srv\homes\\")
        assert cfg.resolve("u", "s") == r"\\srv\homes\u"

    def test_ou_name_is_deepest_ou(self):
        cfg = HomeDirConfig(folder_template="%OU%")
        path = cfg.resolve("u", "s", "OU=Classe,OU=College,DC=x,DC=local")
        assert path == r"\\srv\homes\Classe"


class TestHomeDirConfigValidate:
    def test_default_config_is_valid(self):
        assert HomeDirConfig().validate() == []

    def test_empty_share_root(self):
        assert HomeDirConfig(share_root="  ").validate()

    def test_non_unc_share_root(self):
        errors = HomeDirConfig(share_root="C:/homes").validate()
        assert any("UNC" in e for e in errors)

    def test_bad_drive_letter(self):
        errors = HomeDirConfig(drive_letter="HOME").validate()
        assert any("H:" in e for e in errors)

    def test_empty_template(self):
        assert HomeDirConfig(folder_template="").validate()


class TestHomeDirPlan:
    def test_ad_attributes(self):
        plan = HomeDirPlan(
            user_dn="CN=x,DC=lycee,DC=local",
            sam="jdupont",
            username="Dupont Jean",
            home_path=r"\\srv\homes\Dupont Jean",
            drive_letter="H:",
        )
        assert plan.ad_attributes == {
            "homeDirectory": r"\\srv\homes\Dupont Jean",
            "homeDrive": "H:",
        }

    def test_plan_for_user(self):
        mgr = HomeDirManager(HomeDirConfig(folder_template="%SAM%"))
        plan = mgr.plan_for_user("CN=x,DC=l,DC=local", "Dupont Jean", "jdupont", OU_ELEVE, "lycee.local")
        assert plan.home_path == r"\\srv\homes\jdupont"
        assert plan.drive_letter == "H:"

    def test_plan_batch(self):
        mgr = HomeDirManager(HomeDirConfig(folder_template="%SAM%"))
        plans = mgr.plan_batch(USERS, OU_ELEVE, "lycee.local")
        assert len(plans) == 2
        assert plans[0].home_path == r"\\srv\homes\jdupont"
        assert plans[1].home_path == r"\\srv\homes\lmartin"


class TestNtfsRights:
    def test_icacls_command_contains_three_grants(self):
        mgr = HomeDirManager()
        cmd = mgr.icacls_command(r"\\srv\homes\jdupont", r"lycee\jdupont")
        assert cmd[0] == "icacls"
        assert r"lycee\jdupont:(OI)(CI)M" in cmd
        assert r"BUILTIN\Administrators:(OI)(CI)F" in cmd
        assert "SYSTEM:(OI)(CI)F" in cmd

    def test_acl_lines_use_domain_prefix(self):
        mgr = HomeDirManager(HomeDirConfig(folder_template="%SAM%"))
        plans = mgr.plan_batch(USERS, OU_ELEVE, "lycee.local")
        cmds = mgr.acl_lines(plans, domain="lycee.local")
        assert len(cmds) == 2
        assert r"lycee.local\jdupont:(OI)(CI)M" in cmds[0]


class TestPowerShellScript:
    def test_script_creates_folders_and_sets_acls(self):
        mgr = HomeDirManager(HomeDirConfig(folder_template="%SAM%"))
        plans = mgr.plan_batch(USERS, OU_ELEVE, "lycee.local")
        script = mgr.powershell_script(plans, domain="lycee.local")
        assert "New-Item" in script
        assert "Set-Acl" in script
        assert "Modify" in script
        assert "FullControl" in script
        assert r"\\srv\homes\jdupont" in script
        assert r"lycee.local\jdupont" in script
        assert script.count("$path =") == 2

    def test_script_written_with_utf8_bom(self, tmp_path):
        mgr = HomeDirManager()
        plans = [mgr.plan_for_user("CN=x", "Jean", "jdupont")]
        dest = tmp_path / "scripts" / "homedirs.ps1"
        mgr.save_powershell_script(plans, dest, domain="lycee.local")
        content = dest.read_bytes()
        assert content.startswith(b"\xef\xbb\xbf")  # BOM pour PowerShell
        assert "New-Item" in content.decode("utf-8-sig")


class TestPersistence:
    def test_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.homedirs as mod
        monkeypatch.setattr(mod, "HOME_CONFIG_FILE", tmp_path / "home_dir_config.json")

        cfg = HomeDirConfig(
            share_root=r"\\fs02\homes$",
            drive_letter="W:",
            folder_template="%SAM%",
            apply_on_create=False,
        )
        save_home_dir_config(cfg)
        loaded = load_home_dir_config()
        assert loaded == cfg

    def test_load_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        import edusync_ad.core.homedirs as mod
        monkeypatch.setattr(mod, "HOME_CONFIG_FILE", tmp_path / "absent.json")
        assert load_home_dir_config() == HomeDirConfig()

    def test_load_corrupted_file_returns_defaults(self, tmp_path, monkeypatch):
        import edusync_ad.core.homedirs as mod
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(mod, "HOME_CONFIG_FILE", bad)
        assert load_home_dir_config() == HomeDirConfig()
