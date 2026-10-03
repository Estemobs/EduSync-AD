"""Tests du module Scripts logon/logoff (M15)."""

from __future__ import annotations

from edusync_ad.core.logon_scripts import (
    SCRIPT_VARIABLES,
    LogonScript,
    LogonScriptManager,
    LogonScriptTemplate,
    ScriptPlan,
    build_context,
    deepest_ou_name,
    deploy_scripts,
    group_cn,
    load_logon_scripts,
    netlogon_path,
    render_script,
    save_logon_scripts,
    script_file_encoding,
    unknown_variables,
)

OU_ELEVE = "OU=6emeA,OU=Eleves,DC=lycee,DC=local"
OU_PARENT = "OU=Eleves,DC=lycee,DC=local"
GROUP_PROFS = "CN=Profs,DC=lycee,DC=local"

USER = {
    "dn": "CN=Dupont Jean,OU=6emeA,OU=Eleves,DC=lycee,DC=local",
    "sam": "jdupont",
    "cn": "Dupont Jean",
    "mail": "jdupont@lycee.local",
    "homeDirectory": r"\\srv\homes\jdupont",
}


def make_script(**kwargs) -> LogonScript:
    data = dict(
        name="accueil",
        kind="bat",
        timing="logon",
        scope_type="default",
        scope_dn="",
        content="@echo off\r\necho Bonjour %USERNAME%\r\n",
    )
    data.update(kwargs)
    return LogonScript(**data)


class TestHelpers:
    def test_deepest_ou_name(self):
        assert deepest_ou_name(OU_ELEVE) == "6emeA"
        assert deepest_ou_name("CN=x,DC=lycee,DC=local") == ""
        assert deepest_ou_name("") == ""

    def test_group_cn(self):
        assert group_cn(GROUP_PROFS) == "Profs"
        assert group_cn("6emeA") == "6emeA"


class TestBuildContext:
    def test_all_roadmap_variables(self):
        ctx = build_context(USER, OU_ELEVE, [GROUP_PROFS], "lycee.local")
        assert ctx["%USERNAME%"] == "jdupont"
        assert ctx["%FULLNAME%"] == "Dupont Jean"
        assert ctx["%OU%"] == "6emeA"
        assert ctx["%GROUP%"] == "Profs"
        assert ctx["%EMAIL%"] == "jdupont@lycee.local"
        assert ctx["%HOMEDIR%"] == r"\\srv\homes\jdupont"
        # toutes les variables du roadmap sont couvertes
        assert set(SCRIPT_VARIABLES) <= set(ctx)

    def test_missing_group_gives_empty(self):
        ctx = build_context(USER, OU_ELEVE, [], "lycee.local")
        assert ctx["%GROUP%"] == ""

    def test_ou_taken_from_user_dn_when_not_given(self):
        ctx = build_context(USER, "", [], "")
        assert ctx["%OU%"] == "6emeA"


class TestRender:
    def test_replaces_known_variables(self):
        content = "echo %USERNAME% - %OU% - %EMAIL%"
        out = render_script(content, build_context(USER, OU_ELEVE, [GROUP_PROFS]))
        assert out == "echo jdupont - 6emeA - jdupont@lycee.local"

    def test_keeps_native_windows_variables(self):
        content = "echo %USERNAME% %USERDOMAIN% %HOMEDRIVE%%HOMEPATH%"
        out = render_script(content, build_context(USER, OU_ELEVE, []))
        assert "%USERDOMAIN%" in out
        assert "%HOMEDRIVE%" in out
        assert "jdupont" in out and "%USERNAME%" not in out

    def test_unknown_variables_report(self):
        assert unknown_variables("%USERNAME% %FOO% %BAR%") == ["%BAR%", "%FOO%"]
        assert unknown_variables("%USERNAME%") == []

    def test_builtin_templates_only_use_known_variables(self):
        for tpl in LogonScriptTemplate.builtin():
            assert unknown_variables(tpl.content) == [], tpl.name

    def test_builtin_templates_exist(self):
        names = [t.name for t in LogonScriptTemplate.builtin()]
        assert "Message d'accueil" in names
        assert "Mapping du lecteur H:" in names


class TestValidation:
    def test_valid_script(self):
        assert make_script().validate() == []

    def test_empty_name(self):
        assert make_script(name="  ").validate()

    def test_path_separator_rejected(self):
        errors = make_script(name="..\\evil").validate()
        assert any("séparateur" in e for e in errors)

    def test_bad_kind(self):
        assert make_script(kind="exe").validate()

    def test_bad_timing(self):
        assert make_script(timing="startup").validate()

    def test_scope_ou_without_dn(self):
        assert make_script(scope_type="ou", scope_dn="").validate()

    def test_empty_content(self):
        assert make_script(content="   ").validate()

    def test_filename_normalization(self):
        assert make_script(name="accueil").filename == "accueil.bat"
        assert make_script(name="hello.ps1", kind="ps1").filename == "hello.ps1"
        assert make_script(name="rapport final", kind="vbs").filename == "rapport final.vbs"


class TestEncoding:
    def test_bat_has_no_bom(self):
        enc, bom = script_file_encoding("bat")
        assert enc == "utf-8" and bom is False

    def test_ps1_has_bom(self):
        enc, bom = script_file_encoding("ps1")
        assert enc == "utf-8-sig" and bom is True

    def test_vbs_has_no_bom(self):
        _, bom = script_file_encoding("vbs")
        assert bom is False


class TestDeploy:
    def test_writes_files_with_correct_encoding(self, tmp_path):
        scripts = [
            make_script(name="accueil", kind="bat", content="@echo off\r\n"),
            make_script(name="journal", kind="ps1", content="Write-Host 'hé'\r\n"),
        ]
        written = deploy_scripts(scripts, tmp_path)
        assert len(written) == 2
        bat = (tmp_path / "accueil.bat").read_bytes()
        ps1 = (tmp_path / "journal.ps1").read_bytes()
        assert not bat.startswith(b"\xef\xbb\xbf")   # BOM casserait cmd.exe
        assert ps1.startswith(b"\xef\xbb\xbf")        # BOM utile pour PowerShell

    def test_creates_missing_dir(self, tmp_path):
        dest = tmp_path / "NETLOGON"
        deploy_scripts([make_script()], dest)
        assert (dest / "accueil.bat").exists()


class TestNetlogon:
    def test_netlogon_path(self):
        assert netlogon_path("lycee.local") == r"\\lycee\NETLOGON"
        assert netlogon_path("") == ""

    def test_gpo_logoff_note_mentions_gpo(self):
        from edusync_ad.core.logon_scripts import gpo_logoff_note
        note = gpo_logoff_note(make_script(timing="logoff", name="fin"))
        assert "GPO" in note
        assert "fin.bat" in note
        assert "scriptPath" in note


class TestResolution:
    def _manager(self) -> LogonScriptManager:
        return LogonScriptManager([
            make_script(name="defaut", scope_type="default"),
            make_script(name="eleves", scope_type="ou", scope_dn=OU_ELEVE),
            make_script(name="profs", scope_type="group", scope_dn=GROUP_PROFS),
        ])

    def test_group_priority_over_ou(self):
        mgr = self._manager()
        script = mgr.resolve_for_user(OU_ELEVE, [GROUP_PROFS])
        assert script is not None and script.name == "profs"

    def test_ou_priority_over_default(self):
        mgr = self._manager()
        script = mgr.resolve_for_user(OU_ELEVE)
        assert script is not None and script.name == "eleves"

    def test_parent_ou_fallback(self):
        mgr = self._manager()
        script = mgr.resolve_for_user(OU_ELEVE)
        assert script is not None
        # une OU fille sans script → remonte à la parente ou défaut
        mgr2 = LogonScriptManager([
            make_script(name="defaut", scope_type="default"),
            make_script(name="parents", scope_type="ou", scope_dn=OU_PARENT),
        ])
        assert mgr2.resolve_for_user(OU_ELEVE).name == "parents"

    def test_default_fallback(self):
        mgr = self._manager()
        assert mgr.resolve_for_user("OU=Inconnue,DC=lycee,DC=local").name == "defaut"

    def test_logoff_scripts_never_resolve_for_script_path(self):
        mgr = LogonScriptManager([
            make_script(name="fin", timing="logoff", scope_type="default"),
        ])
        assert mgr.resolve_for_user(OU_ELEVE) is None
        assert mgr.logon_scripts == []

    def test_plans_for_users(self):
        mgr = self._manager()
        users = [
            {"dn": "CN=A," + OU_ELEVE, "sam": "aaa"},
            {"dn": "CN=B," + OU_ELEVE, "sam": "bbb"},
        ]
        groups = {"aaa": [GROUP_PROFS], "bbb": []}
        plans = mgr.plans_for_users(users, OU_ELEVE, groups)
        assert len(plans) == 2
        assert plans[0].script.name == "profs"
        assert plans[1].script.name == "eleves"
        assert plans[0].script_path_value == "profs.bat"

    def test_plan_without_script_clears_path(self):
        plan = ScriptPlan(user_dn="CN=x", sam="x", script=None)
        assert plan.script_path_value == ""


class TestPersistence:
    def test_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.logon_scripts as mod
        monkeypatch.setattr(mod, "SCRIPT_CONFIG_FILE", tmp_path / "scripts.json")

        scripts = [
            make_script(name="accueil"),
            make_script(name="fin", timing="logoff", kind="ps1", scope_type="ou", scope_dn=OU_ELEVE),
        ]
        save_logon_scripts(scripts)
        loaded = load_logon_scripts()
        assert len(loaded) == 2
        assert loaded[0].content == scripts[0].content
        assert loaded[1].timing == "logoff"
        assert loaded[1].scope_dn == OU_ELEVE

    def test_load_missing_file(self, tmp_path, monkeypatch):
        import edusync_ad.core.logon_scripts as mod
        monkeypatch.setattr(mod, "SCRIPT_CONFIG_FILE", tmp_path / "absent.json")
        assert load_logon_scripts() == []

    def test_load_corrupted_file(self, tmp_path, monkeypatch):
        import edusync_ad.core.logon_scripts as mod
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "SCRIPT_CONFIG_FILE", bad)
        assert load_logon_scripts() == []
