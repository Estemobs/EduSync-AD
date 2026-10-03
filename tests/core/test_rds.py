"""Tests du module RDS / Bureau à distance (M21)."""

from __future__ import annotations

import pytest

from edusync_ad.core.rds import (
    PROFILE_MODES,
    CollectionPlan,
    RDSConfig,
    RemoteAppPlan,
    build_collection_plan,
    collection_script,
    fslogix_script,
    load_rds_config,
    load_rds_plans,
    make_alias,
    normalize_group_ref,
    remoteapp_script,
    save_rds_config,
    save_rds_plans,
    split_multi,
    validate_collection,
    validate_remoteapp,
)


def upd_config(**overrides) -> RDSConfig:
    data = {
        "broker": "rdcb.lycee.local",
        "domain": "LYCEE",
        "profile_mode": "upd",
        "upd_path": "\\\\sr01\\UPD$",
        "upd_size_gb": 25,
        "upd_include_paths": ["AppData\\Roaming"],
        "upd_exclude_paths": ["AppData\\Local\\Temp"],
    }
    data.update(overrides)
    return RDSConfig(**data)


def plain_config(**overrides) -> RDSConfig:
    data = {"broker": "rdcb.lycee.local", "domain": "LYCEE"}
    data.update(overrides)
    return RDSConfig(**data)


def fslogix_config(**overrides) -> RDSConfig:
    data = {
        "broker": "rdcb.lycee.local",
        "profile_mode": "fslogix",
        "fslogix_locations": ["\\\\sr01\\FSLogix$", "\\\\sr02\\FSLogix$"],
        "fslogix_size_mb": 25000,
        "fslogix_flipflop": True,
    }
    data.update(overrides)
    return RDSConfig(**data)


def sample_plan(config: RDSConfig | None = None) -> CollectionPlan:
    return build_collection_plan(
        "Edu-Sciences",
        "Salle PC sciences",
        ["rdsh01.lycee.local", "rdsh01.lycee.local", "rdsh02.lycee.local;"],
        ["Profs", "LYCEE\\Eleves"],
        config or upd_config(),
    )


def sample_app() -> RemoteAppPlan:
    return RemoteAppPlan(
        alias="winword",
        display_name="Word 2021",
        file_path="C:\\Program Files\\MSOffice\\WINWORD.EXE",
        collection="Edu-Sciences",
        user_groups=["LYCEE\\Profs"],
        folder_name="Bureautique",
        icon_index=3,
    )


class TestConfig:
    def test_default_valid_without_profiles(self):
        cfg = RDSConfig(broker="rdcb.lycee.local")
        assert cfg.validate() == []
        assert cfg.profile_mode == "none"

    def test_missing_broker(self):
        assert any("Broker" in e for e in RDSConfig().validate())

    def test_broker_with_space(self):
        errors = RDSConfig(broker="rd cb.local").validate()
        assert any("FQDN" in e for e in errors)

    def test_upd_needs_unc_path(self):
        errors = upd_config(upd_path="E:\\UPD").validate()
        assert any("UNC" in e for e in errors)

    def test_upd_needs_positive_size(self):
        errors = upd_config(upd_size_gb=0).validate()
        assert any("supérieure à 0" in e for e in errors)

    def test_fslogix_needs_locations(self):
        errors = fslogix_config(fslogix_locations=[]).validate()
        assert any("VHDLocations" in e for e in errors)

    def test_fslogix_rejects_non_unc(self):
        errors = fslogix_config(fslogix_locations=["/srv/x"]).validate()
        assert any("non-UNC" in e for e in errors)

    def test_unknown_profile_mode(self):
        errors = RDSConfig(broker="b", profile_mode="citrix").validate()
        assert any("Mode de profil" in e for e in errors)

    def test_dict_round_trip(self):
        cfg = upd_config(upd_size_gb=40, upd_include_paths=["A", "B"])
        assert RDSConfig.from_dict(cfg.to_dict()) == cfg

    def test_from_dict_partial(self):
        cfg = RDSConfig.from_dict({"broker": "b1", "unknown": 1})
        assert cfg.broker == "b1"
        assert cfg.profile_mode == "none"

    def test_persistence(self, tmp_path, monkeypatch):
        import edusync_ad.core.rds as mod

        monkeypatch.setattr(mod, "RDS_CONFIG_FILE", tmp_path / "rds.json")
        cfg = upd_config(broker="b2.lycee.local")
        save_rds_config(cfg)
        assert load_rds_config() == cfg

    def test_missing_and_corrupted(self, tmp_path, monkeypatch):
        import edusync_ad.core.rds as mod

        monkeypatch.setattr(mod, "RDS_CONFIG_FILE", tmp_path / "absent.json")
        assert load_rds_config() == RDSConfig()
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "RDS_CONFIG_FILE", bad)
        assert load_rds_config() == RDSConfig()

    def test_profile_modes_catalog(self):
        assert set(PROFILE_MODES) == {"none", "upd", "fslogix"}


class TestHelpers:
    def test_split_multi(self):
        assert split_multi("a ; b, c;; a") == ["a", "b", "c"]
        assert split_multi("") == []
        assert split_multi(None or "") == []

    def test_normalize_group_ref(self):
        assert normalize_group_ref("Eleves", "LYCEE") == "LYCEE\\Eleves"
        assert normalize_group_ref("LYCEE\\Profs", "") == "LYCEE\\Profs"
        assert normalize_group_ref("DOM\\Groupe", "LYCEE") == "DOM\\Groupe"
        assert normalize_group_ref("", "LYCEE") == ""

    def test_make_alias(self):
        assert make_alias("Micro-Soft Office!") == "micro-soft-office"
        assert make_alias("Salle Info 2") == "salle-info-2"
        assert make_alias("  Déjà Vu  ") == "d-j-vu"
        assert make_alias("###") == ""


class TestCollectionPlan:
    def test_dedup_hosts_and_trailing_junk(self):
        plan = sample_plan()
        assert plan.session_hosts == ["rdsh01.lycee.local", "rdsh02.lycee.local"]

    def test_groups_normalized_and_deduped(self):
        plan = sample_plan()
        assert plan.user_groups == ["LYCEE\\Profs", "LYCEE\\Eleves"]

    def test_label(self):
        plan = sample_plan()
        assert "Edu-Sciences" in plan.label
        assert "2 hôte(s)" in plan.label
        assert "2 groupe(s)" in plan.label

    def test_validate_collection(self):
        cfg = plain_config()
        assert validate_collection(sample_plan(), cfg) == []
        errors = validate_collection(CollectionPlan(), cfg)
        assert any("Nom de collection" in e for e in errors)
        assert any("hébergeur" in e for e in errors)
        assert any("Broker" in e for e in validate_collection(sample_plan(), RDSConfig()))


class TestCollectionScript:
    def test_header_and_broker(self):
        script = collection_script([sample_plan()], upd_config())
        assert script.startswith("#Requires -RunAsAdministrator")
        assert "$broker = 'rdcb.lycee.local'" in script
        assert "Collections : 1" in script

    def test_idempotent_creation(self):
        script = collection_script([sample_plan()], upd_config())
        assert "Get-RDSessionCollection -ConnectionBroker $broker" in script
        assert "Where-Object { $_.CollectionName -eq $collectionName }" in script
        assert "New-RDSessionCollection -CollectionName $collectionName" in script
        assert "-CollectionDescription 'Salle PC sciences'" in script
        assert "-SessionHost @('rdsh01.lycee.local', 'rdsh02.lycee.local')" in script

    def test_existing_branch_adds_missing_hosts(self):
        script = collection_script([sample_plan()], upd_config())
        assert "} else {" in script
        assert "Get-RDSessionHost -CollectionName $collectionName" in script
        assert "New-RDSessionHost -CollectionName $collectionName" in script
        assert "if ($hosts -notcontains $h)" in script

    def test_no_description_when_empty(self):
        plan = build_collection_plan("C1", "", ["rdsh01.lycee.local"], [], plain_config())
        script = collection_script([plan], plain_config())
        assert "-CollectionDescription" not in script

    def test_user_groups_replacement_warning(self):
        script = collection_script([sample_plan()], upd_config())
        assert "-UserGroup @('LYCEE\\Profs', 'LYCEE\\Eleves')" in script
        assert "Domain Users" in script  # avertissement de remplacement

    def test_no_groups_no_set_configuration(self):
        plan = build_collection_plan("C1", "", ["rdsh01.lycee.local"], [], plain_config())
        script = collection_script([plan], plain_config())
        assert "-UserGroup" not in script

    def test_upd_block_present(self):
        script = collection_script([sample_plan()], upd_config())
        assert "-EnableUserProfileDisk" in script
        assert "-MaxUserProfileDiskSizeGB 25" in script
        assert "-DiskPath '\\\\sr01\\UPD$'" in script
        assert "-IncludeFolderPath @('AppData\\Roaming')" in script
        assert "-ExcludeFolderPath @('AppData\\Local\\Temp')" in script

    def test_no_upd_by_default(self):
        script = collection_script([sample_plan()], plain_config())
        assert "-EnableUserProfileDisk" not in script
        assert "profils : Aucun" in script

    def test_multiple_collections(self):
        plans = [sample_plan(),
                 build_collection_plan("Edu-Lettres", "", ["rdsh03.lycee.local"], [], plain_config())]
        script = collection_script(plans, plain_config())
        assert script.count("New-RDSessionCollection") == 2
        assert "Collections : 2" in script

    def test_script_ends_with_confirmation(self):
        script = collection_script([sample_plan()], plain_config())
        assert script.rstrip().endswith("'Collections RDS configurées.'")

    def test_errors_raise(self):
        with pytest.raises(ValueError, match="Aucune"):
            collection_script([], upd_config())
        with pytest.raises(ValueError, match="Broker"):
            collection_script([sample_plan()], RDSConfig(profile_mode="upd"))
        with pytest.raises(ValueError, match="hébergeur"):
            collection_script([CollectionPlan(name="X")], plain_config())


class TestRemoteAppScript:
    def test_idempotent_new_and_set(self):
        script = remoteapp_script([sample_app()], plain_config())
        assert "Get-RDRemoteApp -CollectionName $collectionName" in script
        assert "$_.Alias -eq $alias" in script
        assert "New-RDRemoteApp -CollectionName $collectionName -Alias $alias" in script
        assert "Set-RDRemoteApp -CollectionName $collectionName -Alias $alias" in script

    def test_parameters(self):
        script = remoteapp_script([sample_app()], plain_config())
        body = script.split("#>", 1)[1]
        assert "-DisplayName 'Word 2021'" in body
        assert "-FilePath 'C:\\Program Files\\MSOffice\\WINWORD.EXE'" in body
        assert "-UserGroups @('LYCEE\\Profs')" in body
        assert "-FolderName 'Bureautique'" in body
        assert "-IconIndex 3" in body
        assert "-ShowInWebAccess $true" in body

    def test_no_groups_no_usergroups_parameter(self):
        app = RemoteAppPlan(
            alias="salle1", display_name="Salle 1",
            file_path="\\\\srv\\apps\\client.exe", collection="Edu-Sciences",
            required_command_line="/server=SRV1", show_in_web=False,
        )
        script = remoteapp_script([app], plain_config())
        body = script.split("#>", 1)[1]
        assert "-UserGroups" not in body
        assert "-ShowInWebAccess $false" in body
        assert "-CommandLineSetting AllowOnlySpecifiedCommandLine" in body
        assert "-RequiredCommandLine '/server=SRV1'" in body

    def test_errors_raise(self):
        with pytest.raises(ValueError, match="Aucun"):
            remoteapp_script([], plain_config())
        with pytest.raises(ValueError, match="Broker"):
            remoteapp_script([sample_app()], RDSConfig())
        with pytest.raises(ValueError, match="invalide"):
            remoteapp_script([RemoteAppPlan(alias="bad alias!")], plain_config())
        with pytest.raises(ValueError, match="collection"):
            remoteapp_script(
                [RemoteAppPlan(alias="a", display_name="A", file_path="C:\\a.exe")],
                plain_config(),
            )


class TestValidateRemoteApp:
    def test_valid(self):
        assert validate_remoteapp(sample_app()) == []

    def test_missing_fields(self):
        errors = validate_remoteapp(RemoteAppPlan())
        joined = " ".join(errors)
        assert "Alias" in joined and "affichage" in joined
        assert "exécutable" in joined and "collection" in joined

    def test_bad_alias_chars(self):
        errors = validate_remoteapp(
            RemoteAppPlan(alias="a b", display_name="X",
                          file_path="C:\\x.exe", collection="C")
        )
        assert any("invalide" in e for e in errors)


class TestFslogixScript:
    def test_registry_keys(self):
        script = fslogix_script(fslogix_config())
        assert script.startswith("#Requires -RunAsAdministrator")
        assert "'HKLM:\\SOFTWARE\\FSLogix\\Profiles'" in script
        assert "New-Item -Path $path -Force" in script
        assert "-Name 'Enabled' -Value 1 -Type DWord" in script
        assert "-Name 'SizeInMB' -Value 25000 -Type DWord" in script
        assert "-Name 'DeleteLocalProfileWhenVHDShouldApply'" in script
        assert "-Name 'FlipFlopProfileDir' -Value 1 -Type DWord" in script

    def test_vhd_locations_multistring(self):
        script = fslogix_script(fslogix_config())
        assert (
            "@('\\\\sr01\\FSLogix$', '\\\\sr02\\FSLogix$') -Type MultiString"
        ) in script

    def test_flipflop_optional(self):
        script = fslogix_script(fslogix_config(fslogix_flipflop=False))
        assert "FlipFlopProfileDir" not in script

    def test_mentions_reboot(self):
        script = fslogix_script(fslogix_config())
        assert "redémarrage" in script

    def test_errors_raise(self):
        with pytest.raises(ValueError, match="pas FSLogix"):
            fslogix_script(upd_config())
        with pytest.raises(ValueError, match="VHD"):
            fslogix_script(fslogix_config(fslogix_locations=[]))
        with pytest.raises(ValueError, match="non-UNC"):
            fslogix_script(fslogix_config(fslogix_locations=["//srv/x"]))
        with pytest.raises(ValueError, match="supérieure"):
            fslogix_script(fslogix_config(fslogix_size_mb=0))


class TestPlansPersistence:
    def test_round_trip(self, tmp_path, monkeypatch):
        import edusync_ad.core.rds as mod

        monkeypatch.setattr(mod, "RDS_PLANS_FILE", tmp_path / "plans.json")
        plan, app = sample_plan(), sample_app()
        save_rds_plans([plan], [app])
        cols, apps = load_rds_plans()
        assert cols == [plan]
        assert apps == [app]

    def test_missing_and_corrupted(self, tmp_path, monkeypatch):
        import edusync_ad.core.rds as mod

        monkeypatch.setattr(mod, "RDS_PLANS_FILE", tmp_path / "absent.json")
        assert load_rds_plans() == ([], [])
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "RDS_PLANS_FILE", bad)
        assert load_rds_plans() == ([], [])

    def test_unknown_keys_ignored(self, tmp_path, monkeypatch):
        import json

        import edusync_ad.core.rds as mod

        monkeypatch.setattr(mod, "RDS_PLANS_FILE", tmp_path / "plans.json")
        mod.RDS_PLANS_FILE.write_text(
            json.dumps({"collections": [{"name": "C1", "zzz": 1}], "remoteapps": []}),
            encoding="utf-8",
        )
        cols, _ = load_rds_plans()
        assert cols[0].name == "C1"
        assert cols[0].session_hosts == []
