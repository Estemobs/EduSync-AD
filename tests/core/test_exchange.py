"""Tests du module Microsoft Exchange (M20)."""

from __future__ import annotations

from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.exchange import (
    ALIAS_PATTERNS,
    AliasResult,
    ExchangeConfig,
    ExchangeManager,
    MailboxPlan,
    address_report_csv,
    build_group_address_plans,
    build_mailbox_plans,
    email_policy_script,
    load_exchange_config,
    normalize_local_part,
    onprem_script,
    online_script,
    save_exchange_config,
    save_script,
    split_display_name,
    warning_quota,
)


def onprem_config(**overrides) -> ExchangeConfig:
    data = {
        "mode": "onprem",
        "smtp_domain": "lycee.fr",
        "exchange_server": "exch01.lycee.local",
        "mailbox_quota": "10GB",
    }
    data.update(overrides)
    return ExchangeConfig(**data)


def online_config(**overrides) -> ExchangeConfig:
    data = {
        "mode": "online",
        "smtp_domain": "lycee.fr",
        "tenant_id": "11111111-2222-3333-4444-555555555555",
        "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "certificate_thumbprint": "ABC123DEF456",
        "organization": "lycee.onmicrosoft.com",
    }
    data.update(overrides)
    return ExchangeConfig(**data)


USERS = [
    {"sam": "jdurand", "cn": "Jean Durand", "dn": "CN=Jean Durand,OU=Profs,DC=lycee,DC=local"},
    {"sam": "amartin", "cn": "Alice Martin (amartin)", "dn": "CN=Alice Martin,OU=6A,DC=lycee,DC=local"},
    {"sam": "", "cn": "Sans identifiant"},
]


class TestConfig:
    def test_onprem_valid(self):
        assert onprem_config().validate() == []

    def test_online_valid(self):
        assert online_config().validate() == []

    def test_missing_smtp_domain(self):
        errors = onprem_config(smtp_domain="").validate()
        assert any("domaine SMTP" in e for e in errors)

    def test_smtp_domain_with_at(self):
        errors = onprem_config(smtp_domain="x@lycee.fr").validate()
        assert any("simple domaine" in e for e in errors)

    def test_bad_quota(self):
        errors = onprem_config(mailbox_quota="beaucoup").validate()
        assert any("Quota invalide" in e for e in errors)

    def test_onprem_needs_server(self):
        errors = onprem_config(exchange_server="").validate()
        assert any("serveur Exchange" in e for e in errors)

    def test_online_needs_app_registration(self):
        errors = online_config(client_id="").validate()
        assert any("client ID" in e for e in errors)
        errors = online_config(certificate_thumbprint="").validate()
        assert any("empreinte" in e for e in errors)

    def test_unknown_mode(self):
        assert any("Mode" in e for e in ExchangeConfig(mode="lotus").validate())

    def test_dict_round_trip(self):
        cfg = onprem_config(
            archive_enabled=True, retention_policy="3 ans", database="MBX01",
            alias_pattern="{nom}.{prenom}",
        )
        assert ExchangeConfig.from_dict(cfg.to_dict()) == cfg

    def test_from_dict_defaults_missing_keys(self):
        cfg = ExchangeConfig.from_dict({"smtp_domain": "lycee.fr"})
        assert cfg.mode == "onprem"
        assert cfg.mailbox_quota == "10GB"

    def test_persistence(self, tmp_path, monkeypatch):
        import edusync_ad.core.exchange as mod

        monkeypatch.setattr(mod, "EXCHANGE_CONFIG_FILE", tmp_path / "ex.json")
        cfg = onprem_config(archive_enabled=True)
        save_exchange_config(cfg)
        assert load_exchange_config() == cfg

    def test_missing_and_corrupted(self, tmp_path, monkeypatch):
        import edusync_ad.core.exchange as mod

        monkeypatch.setattr(mod, "EXCHANGE_CONFIG_FILE", tmp_path / "absent.json")
        assert load_exchange_config() == ExchangeConfig()
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "EXCHANGE_CONFIG_FILE", bad)
        assert load_exchange_config() == ExchangeConfig()

    def test_alias_patterns_catalog(self):
        assert "{identifiant}" in ALIAS_PATTERNS
        assert "{prenom}.{nom}" in ALIAS_PATTERNS


class TestAddresses:
    def test_normalize_local_part(self):
        assert normalize_local_part("Jean Dupont") == "jean.dupont"
        assert normalize_local_part("Élève De La Croix") == "eleve.de.la.croix"
        assert normalize_local_part("  #Déjà  vu!  ") == "deja.vu"
        assert normalize_local_part("???") == "adresse"

    def test_split_display_name_with_duplicate_suffix(self):
        assert split_display_name("Alice Martin (amartin)", "amartin") == ("Alice", "Martin")

    def test_warning_quota(self):
        assert warning_quota("10GB") == "9GB"
        assert warning_quota("500MB") == "450MB"
        assert warning_quota("1B") == "1B"
        assert warning_quota("inconnu") == "inconnu"

    def test_primary_and_alias(self):
        plans = build_mailbox_plans(USERS, onprem_config(), domain="lycee.local")
        jd = plans[0]
        assert jd.primary == "jdurand@lycee.fr"
        assert jd.aliases == ["jean.durand@lycee.fr"]
        assert jd.upn == "jdurand@lycee.local"

    def test_duplicate_alias_suppressed(self):
        # sam identique au motif → pas d'alias doublon
        users = [{"sam": "jean.dupont", "cn": "Jean Dupont", "dn": "CN=x,DC=l,DC=local"}]
        plans = build_mailbox_plans(users, onprem_config(), domain="lycee.local")
        assert plans[0].primary == "jean.dupont@lycee.fr"
        assert plans[0].aliases == []

    def test_identifiant_pattern_produces_no_alias(self):
        cfg = onprem_config(alias_pattern="{identifiant}")
        plans = build_mailbox_plans(USERS, cfg, domain="lycee.local")
        assert plans[0].aliases == []

    def test_nom_prenom_pattern(self):
        cfg = onprem_config(alias_pattern="{nom}.{prenom}")
        plans = build_mailbox_plans(USERS, cfg, domain="lycee.local")
        assert plans[0].aliases == ["durand.jean@lycee.fr"]

    def test_smtp_prefix_case_rules(self):
        plans = build_mailbox_plans(USERS, onprem_config(), domain="lycee.local")
        addresses = plans[0].smtp_addresses
        assert addresses[0] == "SMTP:jdurand@lycee.fr"   # majuscules = principal
        assert addresses[1] == "smtp:jean.durand@lycee.fr"

    def test_without_domain_upn_empty(self):
        plans = build_mailbox_plans(USERS, onprem_config())
        assert plans[0].upn == ""

    def test_label(self):
        plans = build_mailbox_plans(USERS, onprem_config(), domain="lycee.local")
        assert "jdurand → jdurand@lycee.fr" in plans[0].label
        assert "+1 alias" in plans[0].label

    def test_group_plans(self):
        groups = build_group_address_plans(
            [("CN=Conseil de classe,OU=Grp,DC=lycee,DC=local", "Conseil de classe")],
            onprem_config(),
        )
        plan = groups[0]
        assert plan.kind == "group"
        assert plan.primary == "conseildeclasse@lycee.fr"
        assert plan.aliases == ["conseil.de.classe@lycee.fr"]


class TestOnpremScript:
    def _plans(self):
        return build_mailbox_plans(USERS, onprem_config(), domain="lycee.local")

    def test_session_exchange(self):
        script = onprem_script(self._plans(), onprem_config())
        assert script.startswith("#Requires -RunAsAdministrator")
        assert "New-PSSession -ConfigurationName Microsoft.Exchange" in script
        assert "-ComputerName 'exch01.lycee.local'" in script
        assert "Import-PSSession" in script and "Remove-PSSession" in script

    def test_creates_missing_mailbox(self):
        script = onprem_script(self._plans(), onprem_config(database="MBX01"))
        assert "if (-not (Get-Mailbox -Identity $identity" in script
        assert "-Alias 'jdurand' -Database 'MBX01'" in script

    def test_sets_addresses_and_quotas(self):
        script = onprem_script(self._plans(), onprem_config())
        assert "Set-Mailbox -Identity $identity -EmailAddresses @{Replace = $addresses}" in script
        assert "-ProhibitSendReceiveQuota '10GB'" in script
        assert "-IssueWarningQuota '9GB'" in script

    def test_archive_and_retention(self):
        script = onprem_script(
            self._plans(),
            onprem_config(archive_enabled=True, retention_policy="EduSync - 3 ans"),
        )
        assert "-Archive" in script
        assert "RetentionPolicy 'EduSync - 3 ans'" in script

    def test_no_archive_no_retention_by_default(self):
        script = onprem_script(self._plans(), onprem_config())
        assert "Enable-Mailbox -Identity $identity -Archive" not in script
        assert "RetentionPolicy" not in script

    def test_groups_use_distribution_cmdlets(self):
        groups = build_group_address_plans(
            [("CN=Profs,OU=Grp,DC=lycee,DC=local", "Profs")], onprem_config()
        )
        script = onprem_script(groups, onprem_config())
        assert "Enable-DistributionGroup" in script
        assert "Set-DistributionGroup -Identity $group -EmailAddresses" in script
        assert "Enable-Mailbox" not in script

    def test_identity_falls_back_to_smtp_without_domain(self):
        plans = build_mailbox_plans(USERS, onprem_config())
        script = onprem_script(plans, onprem_config())
        assert "$identity = 'jdurand@lycee.fr'" in script


class TestOnlineScript:
    def _plans(self):
        return build_mailbox_plans(USERS, online_config(), domain="lycee.local")

    def test_app_only_connection(self):
        script = online_script(self._plans(), online_config())
        assert script.startswith("#Requires -Modules ExchangeOnlineManagement")
        assert "Connect-ExchangeOnline -Organization 'lycee.onmicrosoft.com'" in script
        assert "-TenantId '11111111-2222-3333-4444-555555555555'" in script
        assert "-CertificateThumbprint 'ABC123DEF456'" in script
        assert "Disconnect-ExchangeOnline" in script

    def test_warns_when_mailbox_absent(self):
        script = online_script(self._plans(), online_config())
        assert "Boîte absente (compte ou licence manquante)" in script

    def test_addresses_and_quotas(self):
        script = online_script(self._plans(), online_config(mailbox_quota="5GB"))
        assert "Set-Mailbox -Identity $identity -EmailAddresses @{Replace = $addresses}" in script
        assert "-ProhibitSendReceiveQuota '5GB'" in script

    def test_groups(self):
        groups = build_group_address_plans(
            [("CN=Eleves,OU=Grp,DC=lycee,DC=local", "Eleves")], online_config()
        )
        script = online_script(groups, online_config())
        assert "Set-DistributionGroup" in script


class TestEmailPolicyScript:
    def test_contains_policy_cmdlets(self):
        script = email_policy_script(onprem_config())
        assert "New-EmailAddressPolicy" in script
        assert "Update-EmailAddressPolicy" in script
        assert "-IncludedDomains 'lycee.fr'" in script

    def test_template_from_pattern(self):
        script = email_policy_script(onprem_config(alias_pattern="{nom}.{prenom}"))
        assert "SMTP:'%s.%g@lycee.fr'" in script

    def test_unmappable_pattern_falls_back_with_warning(self):
        script = email_policy_script(onprem_config(alias_pattern="{identifiant}"))
        assert "SMTP:'%g.%s@lycee.fr'" in script
        assert "à adapter manuellement" in script

    def test_mentions_online_limitation(self):
        script = email_policy_script(online_config(exchange_server="exch01"))
        assert "Exchange Online ne prend plus en charge les EAP" in script


class TestOutputs:
    def test_save_script_uses_bom(self, tmp_path):
        dest = tmp_path / "out.ps1"
        save_script("# script\r\nWrite-Host 'ok'", dest)
        raw = dest.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"  # BOM requis par Windows PowerShell 5.1

    def test_address_report_csv(self, tmp_path):
        plans = build_mailbox_plans(USERS, onprem_config(), domain="lycee.local")
        dest = address_report_csv(plans, tmp_path / "adresses.csv")
        text = dest.read_text(encoding="utf-8-sig")
        lines = text.strip().splitlines()
        assert lines[0] == "Identifiant;Type;SMTP principal;Aliases"
        assert len(lines) == 3
        assert "jdurand;user;jdurand@lycee.fr;jean.durand@lycee.fr" in lines[1]


class FakeAD:
    """Fausse connexion AD qui enregistre les écritures d'attributs."""

    def __init__(self, failing_dn: str = "") -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.failing_dn = failing_dn

    def update_user_attribute(self, dn: str, attribute: str, value) -> None:
        if dn == self.failing_dn:
            raise ADError("Droits insuffisants sur cet objet.")
        self.calls.append((dn, attribute, value))


class TestApplyToAD:
    def _plans(self):
        return build_mailbox_plans(USERS, onprem_config(), domain="lycee.local")

    def test_writes_mail_and_proxy_addresses(self):
        ad = FakeAD()
        results = ExchangeManager(onprem_config(), ad).apply_ad_addresses(self._plans())
        # 2 lignes (mail + proxyAddresses) par compte valide (la ligne sans sam est exclue)
        assert len(results) == 2
        assert all(r.ok for r in results)

        mail_calls = [c for c in ad.calls if c[1] == "mail"]
        proxy_calls = [c for c in ad.calls if c[1] == "proxyAddresses"]
        assert mail_calls[0][2] == "jdurand@lycee.fr"
        assert proxy_calls[0][2] == [
            "SMTP:jdurand@lycee.fr", "smtp:jean.durand@lycee.fr"
        ]

    def test_error_is_reported_not_raised(self):
        ad = FakeAD(failing_dn="CN=Jean Durand,OU=Profs,DC=lycee,DC=local")
        results = ExchangeManager(onprem_config(), ad).apply_ad_addresses(self._plans())
        failed = [r for r in results if not r.ok]
        assert len(failed) == 1
        assert "Droits insuffisants" in failed[0].detail
        assert failed[0].action == "error"
        assert not failed[0].ok

    def test_without_connection(self):
        results = ExchangeManager(onprem_config(), None).apply_ad_addresses(self._plans())
        assert all("aucune connexion AD" in r.detail for r in results)

    def test_without_dn(self):
        plans = [MailboxPlan(kind="user", sam="x", primary="x@lycee.fr")]
        results = ExchangeManager(onprem_config(), FakeAD()).apply_ad_addresses(plans)
        assert "DN inconnu" in results[0].detail

    def test_on_progress_callback(self):
        seen: list[str] = []
        ExchangeManager(onprem_config(), FakeAD()).apply_ad_addresses(
            self._plans(), on_progress=lambda p: seen.append(p.sam)
        )
        assert seen == ["jdurand", "amartin"]

    def test_manager_uses_default_config(self):
        manager = ExchangeManager(None, FakeAD())
        assert isinstance(manager.config, ExchangeConfig)

    def test_build_plans_via_manager(self):
        manager = ExchangeManager(onprem_config(), FakeAD())
        plans = manager.build_plans(USERS, domain="lycee.local")
        assert plans[0].upn == "jdurand@lycee.local"

    def test_result_type(self):
        result = AliasResult("x", "updated", "ok")
        assert result.ok
        assert not AliasResult("x", "error", "ko").ok
