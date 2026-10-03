"""Tests du module Quotas de disque FSRM (M14)."""

from __future__ import annotations

from edusync_ad.core.homedirs import HomeDirPlan
from edusync_ad.core.quotas import (
    GB,
    QuotaManager,
    QuotaPlan,
    QuotaSettings,
    load_all_quota_configs,
    save_all_quota_configs,
)

OU_ELEVE = "OU=6emeA,OU=Eleves,DC=lycee,DC=local"
OU_PARENT = "OU=Eleves,DC=lycee,DC=local"
GROUP_PROFS = "CN=Profs,DC=lycee,DC=local"


def _home(sam: str, path: str) -> HomeDirPlan:
    return HomeDirPlan(
        user_dn=f"CN={sam},DC=lycee,DC=local",
        sam=sam,
        username=sam,
        home_path=path,
        drive_letter="H:",
    )


class TestQuotaSettings:
    def test_default_values(self):
        s = QuotaSettings()
        assert s.size_gb == 10.0
        assert s.warning_percent == 80
        assert s.hard_limit is True

    def test_size_bytes(self):
        assert QuotaSettings(size_gb=10).size_bytes == 10 * GB

    def test_warning_bytes(self):
        assert QuotaSettings(size_gb=10, warning_percent=90).warning_bytes == 9 * GB

    def test_template_name_is_stable(self):
        a = QuotaSettings(size_gb=5, warning_percent=75, hard_limit=True)
        b = QuotaSettings(size_gb=5, warning_percent=75, hard_limit=True)
        assert a.template_name == b.template_name
        assert "5" in a.template_name and "75" in a.template_name

    def test_template_name_differs_for_soft_limit(self):
        hard = QuotaSettings(hard_limit=True).template_name
        soft = QuotaSettings(hard_limit=False).template_name
        assert hard != soft

    def test_validate_ok(self):
        assert QuotaSettings().validate() == []

    def test_validate_bad_size(self):
        assert QuotaSettings(size_gb=0).validate()
        assert QuotaSettings(size_gb=-5).validate()

    def test_validate_bad_warning(self):
        assert QuotaSettings(warning_percent=0).validate()
        assert QuotaSettings(warning_percent=100).validate()
        assert QuotaSettings(warning_percent=120).validate()

    def test_dict_round_trip(self):
        s = QuotaSettings(size_gb=25.5, warning_percent=90, hard_limit=False)
        assert QuotaSettings.from_dict(s.to_dict()) == s


class TestQuotaResolution:
    def test_default_fallback(self):
        mgr = QuotaManager()
        assert mgr.get_settings_for_user(OU_ELEVE) == QuotaSettings()

    def test_ou_config(self):
        mgr = QuotaManager(ou_configs={OU_ELEVE: QuotaSettings(size_gb=5)})
        assert mgr.get_settings_for_user(OU_ELEVE).size_gb == 5

    def test_parent_ou_config(self):
        mgr = QuotaManager(ou_configs={OU_PARENT: QuotaSettings(size_gb=20)})
        assert mgr.get_settings_for_user(OU_ELEVE).size_gb == 20

    def test_group_priority_over_ou(self):
        mgr = QuotaManager(
            ou_configs={OU_ELEVE: QuotaSettings(size_gb=5)},
            group_configs={GROUP_PROFS: QuotaSettings(size_gb=50)},
        )
        settings = mgr.get_settings_for_user(OU_ELEVE, [GROUP_PROFS])
        assert settings.size_gb == 50

    def test_plan_for_user(self):
        mgr = QuotaManager(default_config=QuotaSettings(size_gb=8))
        plan = mgr.plan_for_user("CN=x", "jdupont", r"\\srv\homes\jdupont", OU_ELEVE)
        assert isinstance(plan, QuotaPlan)
        assert plan.settings.size_gb == 8
        assert plan.home_path == r"\\srv\homes\jdupont"
        assert plan.template_name

    def test_plans_for_homes_from_home_plans(self):
        mgr = QuotaManager(default_config=QuotaSettings(size_gb=10))
        homes = [_home("jdupont", r"\\srv\homes\jdupont"), _home("lmartin", r"\\srv\homes\lmartin")]
        plans = mgr.plans_for_homes(homes, OU_ELEVE)
        assert len(plans) == 2
        assert plans[0].sam == "jdupont"
        assert plans[1].home_path == r"\\srv\homes\lmartin"

    def test_plans_for_homes_from_dicts(self):
        mgr = QuotaManager()
        plans = mgr.plans_for_homes(
            [{"user_dn": "CN=x", "sam": "jdupont", "home_path": r"\\s\h\jdupont"}],
            OU_ELEVE,
        )
        assert len(plans) == 1
        assert plans[0].user_dn == "CN=x"


class TestPowerShellScript:
    def _script(self) -> str:
        mgr = QuotaManager(default_config=QuotaSettings(size_gb=10, warning_percent=80))
        homes = [_home("jdupont", r"\\srv\homes\jdupont"), _home("lmartin", r"\\srv\homes\lmartin")]
        return mgr.powershell_script(mgr.plans_for_homes(homes, OU_ELEVE))

    def test_script_contains_fsrm_cmdlets(self):
        script = self._script()
        assert "Import-Module FSRM" in script
        assert "New-FsrmQuotaTemplate" in script
        assert "New-FsrmQuota" in script
        assert "Set-FsrmQuota" in script

    def test_script_defines_template_once(self):
        script = self._script()
        # Un seul template pour des paramètres identiques
        assert script.count("New-FsrmQuotaTemplate") == 1
        assert script.count("New-FsrmQuota -Path") == 2

    def test_script_contains_paths_and_sizes(self):
        script = self._script()
        assert r"\\srv\homes\jdupont" in script
        assert str(10 * GB) in script
        assert "Usage = 80" in script

    def test_soft_limit_flag(self):
        mgr = QuotaManager(default_config=QuotaSettings(hard_limit=False))
        plans = mgr.plans_for_homes([_home("jdupont", r"\\s\h\j")], OU_ELEVE)
        script = mgr.powershell_script(plans)
        assert "-SoftLimit:$true" in script

    def test_hard_limit_default(self):
        mgr = QuotaManager(default_config=QuotaSettings(hard_limit=True))
        plans = mgr.plans_for_homes([_home("jdupont", r"\\s\h\j")], OU_ELEVE)
        script = mgr.powershell_script(plans)
        assert "-SoftLimit:$false" in script

    def test_save_script_utf8_bom(self, tmp_path):
        mgr = QuotaManager()
        plans = mgr.plans_for_homes([_home("jdupont", r"\\s\h\j")], OU_ELEVE)
        dest = tmp_path / "quotas.ps1"
        mgr.save_powershell_script(plans, dest)
        assert dest.read_bytes().startswith(b"\xef\xbb\xbf")


class TestReport:
    def _plans(self) -> list[QuotaPlan]:
        mgr = QuotaManager(default_config=QuotaSettings(size_gb=10, warning_percent=80))
        return mgr.plans_for_homes(
            [
                _home("ok", r"\\s\h\ok"),
                _home("alerte", r"\\s\h\alerte"),
                _home("depasse", r"\\s\h\depasse"),
                _home("sans", r"\\s\h\sans"),
            ],
            OU_ELEVE,
        )

    def test_report_states(self):
        usage = {
            r"\\s\h\ok": 1 * GB,
            r"\\s\h\alerte": 8.5 * GB,
            r"\\s\h\depasse": 11 * GB,
        }
        rows = QuotaManager.report_rows(self._plans(), usage)
        states = {r["identifiant"]: r["etat"] for r in rows}
        assert states["ok"] == "OK"
        assert states["alerte"] == "Alerte"
        assert states["depasse"] == "Dépassé"
        assert states["sans"] == "Non relevé"

    def test_report_columns(self):
        rows = QuotaManager.report_rows(self._plans(), {})
        row = rows[0]
        for col in ("identifiant", "chemin", "quota_go", "alerte_pct", "type",
                    "template", "etat"):
            assert col in row

    def test_report_percent_used(self):
        usage = {r"\\s\h\ok": 5 * GB}
        rows = QuotaManager.report_rows(self._plans(), usage)
        ok = next(r for r in rows if r["identifiant"] == "ok")
        assert ok["occupation_pct"] == 50.0
        assert ok["restant_go"] == "5.00"

    def test_export_csv_semicolon(self, tmp_path):
        rows = QuotaManager.report_rows(self._plans(), {r"\\s\h\ok": 1 * GB})
        dest = tmp_path / "rapport.csv"
        QuotaManager.export_report_csv(rows, dest)
        content = dest.read_text(encoding="utf-8-sig")
        header = content.splitlines()[0]
        assert ";" in header
        assert "identifiant" in header
        assert "etat" in header
        # Une ligne par quota (+ en-tête)
        assert len(content.strip().splitlines()) == len(rows) + 1


class TestPersistence:
    def test_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.quotas as mod
        monkeypatch.setattr(mod, "QUOTA_CONFIG_FILE", tmp_path / "quota_configs.json")

        ou_configs = {OU_ELEVE: QuotaSettings(size_gb=5, warning_percent=70)}
        group_configs = {GROUP_PROFS: QuotaSettings(size_gb=50, hard_limit=False)}
        default = QuotaSettings(size_gb=15)

        save_all_quota_configs(ou_configs, group_configs, default)
        loaded_ou, loaded_group, loaded_default = load_all_quota_configs()

        assert loaded_ou[OU_ELEVE].size_gb == 5
        assert loaded_ou[OU_ELEVE].warning_percent == 70
        assert loaded_group[GROUP_PROFS].hard_limit is False
        assert loaded_default.size_gb == 15

    def test_load_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        import edusync_ad.core.quotas as mod
        monkeypatch.setattr(mod, "QUOTA_CONFIG_FILE", tmp_path / "absent.json")
        ou, group, default = load_all_quota_configs()
        assert ou == {} and group == {}
        assert default == QuotaSettings()

    def test_load_corrupted_file_returns_defaults(self, tmp_path, monkeypatch):
        import edusync_ad.core.quotas as mod
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "QUOTA_CONFIG_FILE", bad)
        assert load_all_quota_configs() == ({}, {}, QuotaSettings())
