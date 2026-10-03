"""Microsoft Exchange (M20) — boîtes aux lettres, aliases, politiques, quotas.

Deux modes, cohérents avec la réalité Exchange :

- **on-prem** — Remote PowerShell vers le serveur Exchange
  (``Enable-Mailbox`` / ``Set-Mailbox`` / ``Set-DistributionGroup``)
- **online** — module ``ExchangeOnlineManagement``
  (``Connect-ExchangeOnline`` en app-only : TenantId + ClientId +
  empreinte de certificat), les boîtes naissent quand la licence est posée

Le module produit des **plans** (SMTP principal + aliases + quota + archive +
rétention) et les **scripts PowerShell** correspondants. Les adresses
(``mail`` / ``proxyAddresses``) peuvent en revanche être écrites **directement
dans l'AD par LDAP** — c'est le volet exécutable depuis n'importe quelle
plateforme : Exchange (on-prem ou hybride) lit ces attributs pour routage.
Les politiques d'adresses (``New-EmailAddressPolicy``) existent uniquement
côté Exchange on-prem ; Exchange Online ne les gère plus, les adresses y sont
posées directement.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from edusync_ad.core.config import config_dir

EXCHANGE_CONFIG_FILE = config_dir() / "exchange.json"

MODES = {"onprem": "Exchange sur site (Remote PowerShell)", "online": "Exchange Online (EXO)"}

ALIAS_PATTERNS: dict[str, str] = {
    "{identifiant}": "identifiant seul (jdurand)",
    "{prenom}.{nom}": "prenom.nom (recommandé)",
    "{nom}.{prenom}": "nom.prenom",
    "{prenom}{nom}": "prenomnom",
    "{prenom}_{nom}": "prenom_nom",
}

# Motifs d'alias → gabarits de politique d'adresses (jetons Exchange : %g = prénom, %s = nom)
_EAP_TOKENS = {
    "{prenom}.{nom}": "%g.%s",
    "{nom}.{prenom}": "%s.%g",
    "{prenom}{nom}": "%g%s",
    "{prenom}_{nom}": "%g_%s",
    "{prenom}-{nom}": "%g-%s",
}


@dataclass
class ExchangeConfig:
    """Paramètres du module Exchange."""

    mode: str = "onprem"
    smtp_domain: str = ""                 # domaine SMTP (ex : lycee.fr)
    alias_pattern: str = "{prenom}.{nom}"
    mailbox_quota: str = "10GB"
    archive_enabled: bool = False
    retention_policy: str = ""
    # on-prem
    exchange_server: str = ""             # FQDN du serveur Exchange
    database: str = ""                    # base de données (Enable-Mailbox)
    # online
    tenant_id: str = ""
    client_id: str = ""
    certificate_thumbprint: str = ""
    organization: str = ""                # ex : lycee.onmicrosoft.com

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.mode not in MODES:
            errors.append("Mode Exchange inconnu (onprem / online).")
        if not self.smtp_domain.strip():
            errors.append("Le domaine SMTP est vide.")
        elif " " in self.smtp_domain or "@" in self.smtp_domain:
            errors.append("Le domaine SMTP doit être un simple domaine (ex : lycee.fr).")
        if self.alias_pattern not in ALIAS_PATTERNS:
            errors.append("Motif d'alias inconnu.")
        if not re.match(r"^\d+\s?(B|KB|MB|GB|TB)$", self.mailbox_quota.strip(), re.I):
            errors.append("Quota invalide (ex : 10GB, 500MB).")
        if self.mode == "onprem":
            if not self.exchange_server.strip():
                errors.append("Le serveur Exchange est vide (mode sur site).")
        else:
            for label, value in (
                ("tenant ID", self.tenant_id),
                ("client ID", self.client_id),
                ("empreinte de certificat", self.certificate_thumbprint),
                ("organisation", self.organization),
            ):
                if not value.strip():
                    errors.append(f"{label} vide (mode Exchange Online).")
        return errors

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "smtp_domain": self.smtp_domain,
            "alias_pattern": self.alias_pattern,
            "mailbox_quota": self.mailbox_quota,
            "archive_enabled": self.archive_enabled,
            "retention_policy": self.retention_policy,
            "exchange_server": self.exchange_server,
            "database": self.database,
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "certificate_thumbprint": self.certificate_thumbprint,
            "organization": self.organization,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExchangeConfig":
        keys = (
            "mode", "smtp_domain", "alias_pattern", "mailbox_quota",
            "archive_enabled", "retention_policy", "exchange_server", "database",
            "tenant_id", "client_id", "certificate_thumbprint", "organization",
        )
        defaults = cls()
        return cls(**{k: data.get(k, getattr(defaults, k)) for k in keys})


def load_exchange_config() -> ExchangeConfig:
    if EXCHANGE_CONFIG_FILE.exists():
        try:
            with EXCHANGE_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                return ExchangeConfig.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError):
            pass
    return ExchangeConfig()


def save_exchange_config(config: ExchangeConfig) -> None:
    EXCHANGE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EXCHANGE_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)


# -- Adresses --------------------------------------------------------------------------

def normalize_local_part(text: str) -> str:
    """Partie locale d'une adresse SMTP : accents retirés, minuscules,
    espaces → points, seuls ``[a-z0-9._-]`` conservés."""
    out = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    out = out.strip().lower().replace(" ", ".")
    out = re.sub(r"[^a-z0-9._-]", "", out)
    out = re.sub(r"\.{2,}", ".", out)
    return out.strip(".") or "adresse"


def split_display_name(cn: str, sam: str = "") -> tuple[str, str]:
    """Découpe un ``cn`` « Prénom Nom » (suffixe de doublon retiré)."""
    text = (cn or "").strip()
    if sam and text.endswith(f"({sam})"):
        text = text[: -len(sam) - 2].strip()
    parts = text.split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _pattern_address(pattern: str, sam: str, prenom: str, nom: str) -> str:
    local = pattern.format(identifiant=sam, prenom=prenom, nom=nom)
    return normalize_local_part(local)


@dataclass
class MailboxPlan:
    """Boîte aux lettres (ou groupe de messagerie) à traiter."""

    kind: str = "user"                    # "user" | "group"
    dn: str = ""
    sam: str = ""
    upn: str = ""                         # identité Exchange (UPN pour les comptes)
    cn: str = ""                          # nom affiché
    prenom: str = ""
    nom: str = ""
    primary: str = ""                     # SMTP principal (sans préfixe)
    aliases: list[str] = field(default_factory=list)   # secondaires (sans préfixe)

    @property
    def smtp_addresses(self) -> list[str]:
        """Liste préfixée ``SMTP:`` / ``smtp:`` attendue par Exchange."""
        addresses = [f"SMTP:{self.primary}"]
        addresses.extend(f"smtp:{alias}" for alias in self.aliases)
        return addresses

    @property
    def label(self) -> str:
        who = self.sam or self.cn
        extra = f" +{len(self.aliases)} alias(es)" if self.aliases else ""
        return f"{who} → {self.primary}{extra}"


def build_mailbox_plans(
    users: Sequence[dict],
    config: ExchangeConfig,
    domain: str = "",
) -> list[MailboxPlan]:
    """Plans de boîtes depuis des comptes AD (sam / cn / dn).

    ``domain`` sert à construire l'identité Exchange (UPN) ; à défaut, c'est
    le SMTP principal qui sert d'identité dans les scripts."""
    plans: list[MailboxPlan] = []
    for user in users:
        sam = str(user.get("sam") or "").strip()
        if not sam:
            continue
        cn = str(user.get("cn") or sam)
        prenom, nom = split_display_name(cn, sam)
        primary = f"{normalize_local_part(sam)}@{config.smtp_domain}"
        aliases: list[str] = []
        if config.alias_pattern != "{identifiant}":
            alias = _pattern_address(config.alias_pattern, sam, prenom, nom)
            candidate = f"{alias}@{config.smtp_domain}"
            if candidate.lower() != primary.lower():
                aliases.append(candidate)
        plans.append(
            MailboxPlan(
                kind="user",
                dn=str(user.get("dn") or ""),
                sam=sam,
                upn=f"{sam}@{domain}" if domain else "",
                cn=cn,
                prenom=prenom,
                nom=nom,
                primary=primary,
                aliases=aliases,
            )
        )
    return plans


def build_group_address_plans(
    groups: Sequence[tuple[str, str]],
    config: ExchangeConfig,
) -> list[MailboxPlan]:
    """Plans de groupes de messagerie depuis ``(dn, nom)`` d'AD."""
    plans: list[MailboxPlan] = []
    for dn, name in groups:
        local = normalize_local_part(name.replace(" ", ""))
        primary = f"{local}@{config.smtp_domain}"
        dotted = normalize_local_part(name)
        aliases: list[str] = []
        if dotted and f"{dotted}@{config.smtp_domain}".lower() != primary.lower():
            aliases.append(f"{dotted}@{config.smtp_domain}")
        plans.append(
            MailboxPlan(kind="group", dn=dn, sam=name, cn=name, primary=primary, aliases=aliases)
        )
    return plans


def warning_quota(quota: str) -> str:
    """Quota d'alerte = 90 % du quota de prohibition (inchangé si non calculable)."""
    match = re.match(r"^(\d+)\s?(B|KB|MB|GB|TB)$", quota.strip(), re.I)
    if not match:
        return quota
    value, unit = int(match.group(1)), match.group(2).upper()
    return f"{max(1, round(value * 0.9))}{unit}"


# -- Résultats ---------------------------------------------------------------------------

@dataclass
class AliasResult:
    """Issue d'une écriture d'adresses dans l'AD."""

    key: str
    action: str          # "updated" | "error"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "error"


# -- Application LDAP -----------------------------------------------------------------------

class ExchangeManager:
    """Applique les plans aux objets AD (mail + proxyAddresses) via LDAP.

    Volet exécutable partout : Exchange lit ces attributs pour le routage
    (scénario hybride / mise en place d'adresses avant boîtes). La création
    effective des boîtes reste du ressort des scripts Exchange (M20)."""

    def __init__(self, config: ExchangeConfig | None = None, connection=None) -> None:
        self.config = config if config is not None else load_exchange_config()
        self._ad = connection

    def build_plans(self, users: Sequence[dict], domain: str = "") -> list[MailboxPlan]:
        return build_mailbox_plans(users, self.config, domain)

    def apply_ad_addresses(
        self,
        plans: Sequence[MailboxPlan],
        on_progress: Callable[[MailboxPlan], None] | None = None,
    ) -> list[AliasResult]:
        """Écrit ``mail`` + ``proxyAddresses`` (SMTP:/smtp:) sur chaque objet."""
        from edusync_ad.core.ad.exceptions import ADError

        results: list[AliasResult] = []
        for plan in plans:
            key = plan.sam or plan.cn or plan.dn
            if self._ad is None:
                results.append(AliasResult(key, "error", "aucune connexion AD"))
            elif not plan.dn:
                results.append(AliasResult(key, "error", "DN inconnu"))
            else:
                try:
                    self._ad.update_user_attribute(plan.dn, "mail", plan.primary)
                    self._ad.update_user_attribute(
                        plan.dn, "proxyAddresses", plan.smtp_addresses
                    )
                    results.append(
                        AliasResult(
                            key, "updated",
                            f"mail={plan.primary} (+{len(plan.aliases)} alias(es))",
                        )
                    )
                except ADError as exc:
                    results.append(AliasResult(key, "error", str(exc)))
            if on_progress:
                on_progress(plan)
        return results


# -- Scripts PowerShell ------------------------------------------------------------------------

def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _addresses_literal(plan: MailboxPlan) -> str:
    return ", ".join(f"'{_ps_quote(a)}'" for a in plan.smtp_addresses)


def _common_mailbox_lines(plan: MailboxPlan, config: ExchangeConfig, identity_var: str) -> list[str]:
    """Quotas + archive + rétention (communs on-prem / online)."""
    quota = config.mailbox_quota
    lines = [
        f"Set-Mailbox -Identity ${identity_var} "
        f"-ProhibitSendReceiveQuota '{quota}' -ProhibitSendQuota '{quota}' "
        f"-IssueWarningQuota '{warning_quota(quota)}'",
    ]
    if config.archive_enabled:
        lines.append(
            f"if (-not (Get-Mailbox -Identity ${identity_var} -Archive "
            f"-ErrorAction SilentlyContinue)) "
            f"{{ Enable-Mailbox -Identity ${identity_var} -Archive }}"
        )
    if config.retention_policy.strip():
        lines.append(
            f"Set-Mailbox -Identity ${identity_var} "
            f"-RetentionPolicy '{_ps_quote(config.retention_policy)}'"
        )
    return lines


def online_script(plans: Sequence[MailboxPlan], config: ExchangeConfig) -> str:
    """Script Exchange Online (module ExchangeOnlineManagement, app-only)."""
    users = [p for p in plans if p.kind == "user"]
    groups = [p for p in plans if p.kind == "group"]
    lines = [
        "#Requires -Modules ExchangeOnlineManagement",
        "<#",
        " Script généré par EduSync-AD — Exchange Online (M20)",
        f" Boîtes : {len(users)} — groupes de messagerie : {len(groups)}",
        f" Quota : {config.mailbox_quota} — archive : "
        f"{'oui' if config.archive_enabled else 'non'} — rétention : "
        f"{config.retention_policy or 'aucune'}",
        " Prérequis : module ExchangeOnlineManagement, certificat d'application",
        " enregistré côté Entra ID avec le rôle « Exchange Administrator ».",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        "",
        "# 1. Connexion app-only",
        f"Connect-ExchangeOnline -Organization '{_ps_quote(config.organization)}' "
        f"-TenantId '{_ps_quote(config.tenant_id)}' "
        f"-ClientId '{_ps_quote(config.client_id)}' "
        f"-CertificateThumbprint '{_ps_quote(config.certificate_thumbprint)}' "
        "-ShowBanner:$false",
        "",
    ]

    for plan in users:
        identity = plan.upn or plan.primary
        lines.extend([
            "# --- " + (plan.sam or plan.cn) + " ---",
            f"$identity = '{_ps_quote(identity)}'",
            "if (-not (Get-Mailbox -Identity $identity -ErrorAction SilentlyContinue)) {",
            "    Write-Warning \"Boîte absente (compte ou licence manquante) : $identity\"",
            "} else {",
            f"    $addresses = @({_addresses_literal(plan)})",
            "    Set-Mailbox -Identity $identity -EmailAddresses @{Replace = $addresses}",
        ])
        for extra in _common_mailbox_lines(plan, config, "identity"):
            lines.append(f"    {extra}")
        lines.append("}")
        lines.append("")

    for plan in groups:
        lines.extend([
            "# --- groupe : " + plan.cn + " ---",
            f"$group = '{_ps_quote(plan.dn or plan.primary)}'",
            "if (-not (Get-DistributionGroup -Identity $group -ErrorAction SilentlyContinue)) {",
            "    Write-Warning \"Groupe de messagerie absent : $group\"",
            "} else {",
            f"    $addresses = @({_addresses_literal(plan)})",
            "    Set-DistributionGroup -Identity $group -EmailAddresses @{Replace = $addresses}",
            "}",
            "",
        ])

    lines.append("Disconnect-ExchangeOnline -Confirm:$false")
    lines.append("Write-Host 'Adresses Exchange Online appliquées.'")
    return "\n".join(lines) + "\n"


def onprem_script(plans: Sequence[MailboxPlan], config: ExchangeConfig) -> str:
    """Script Exchange sur site (Remote PowerShell + création si absence)."""
    users = [p for p in plans if p.kind == "user"]
    groups = [p for p in plans if p.kind == "group"]
    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Exchange sur site (M20)",
        f" Boîtes : {len(users)} — groupes de messagerie : {len(groups)}",
        f" Serveur : {config.exchange_server} — base : {config.database or '(défaut)'}",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        "",
        "# 1. Session Exchange",
        "$session = New-PSSession -ConfigurationName Microsoft.Exchange "
        f"-ComputerName '{_ps_quote(config.exchange_server)}'",
        "Import-PSSession $session -DisableNameChecking",
        "",
    ]

    for plan in users:
        identity = plan.upn or plan.primary
        lines.extend([
            "# --- " + (plan.sam or plan.cn) + " ---",
            f"$identity = '{_ps_quote(identity)}'",
            "if (-not (Get-Mailbox -Identity $identity -ErrorAction SilentlyContinue)) {",
            f"    Enable-Mailbox -Identity $identity -Alias '{_ps_quote(plan.sam or normalize_local_part(plan.cn))}'"
            + (f" -Database '{_ps_quote(config.database)}'" if config.database else ""),
            "}",
            f"$addresses = @({_addresses_literal(plan)})",
            "Set-Mailbox -Identity $identity -EmailAddresses @{Replace = $addresses}",
        ])
        for extra in _common_mailbox_lines(plan, config, "identity"):
            lines.append(extra)
        lines.append("")

    for plan in groups:
        identity = plan.dn or plan.primary
        lines.extend([
            "# --- groupe : " + plan.cn + " ---",
            f"$group = '{_ps_quote(identity)}'",
            "if (-not (Get-DistributionGroup -Identity $group -ErrorAction SilentlyContinue)) {",
            "    Enable-DistributionGroup -Identity $group"
            + (f" -Alias '{_ps_quote(normalize_local_part(plan.cn))}'" if plan.sam else ""),
            "}",
            f"$addresses = @({_addresses_literal(plan)})",
            "Set-DistributionGroup -Identity $group -EmailAddresses @{Replace = $addresses}",
            "",
        ])

    lines.append("Remove-PSSession $session")
    lines.append("Write-Host 'Adresses Exchange appliquées.'")
    return "\n".join(lines) + "\n"


def email_policy_script(config: ExchangeConfig) -> str:
    """Politique d'adresses (Exchange on-prem uniquement — EAP)."""
    template = _EAP_TOKENS.get(config.alias_pattern, "%g.%s")
    note = (
        ""
        if config.alias_pattern in _EAP_TOKENS
        else f"# Motif {config.alias_pattern} non exprimable en jeton EAP : "
             "gabarit proposé %g.%s (prénom.nom) à adapter manuellement."
    )
    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Politique d'adresses (M20)",
        f" Domaine SMTP : {config.smtp_domain}",
        " Exchange Online ne prend plus en charge les EAP : dans ce mode,",
        " les adresses sont posées par les scripts de boîtes ci-dessus.",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        "$session = New-PSSession -ConfigurationName Microsoft.Exchange "
        f"-ComputerName '{_ps_quote(config.exchange_server)}'",
        "Import-PSSession $session -DisableNameChecking",
        "",
        "$policyName = 'EduSync-AD — politique principale'",
        "if (-not (Get-EmailAddressPolicy -Identity $policyName -ErrorAction SilentlyContinue)) {",
        "    New-EmailAddressPolicy -Name $policyName `",
        f"        -IncludedDomains '{_ps_quote(config.smtp_domain)}' `",
        "        -Priority 1 `",
        "        -LdapRecipientFilter \"(&(objectCategory=person)(objectClass=user)(mailNickname=*))\" `",
        f"        -EnabledEmailAddressTemplates @{{ SMTP:'{template}@{_ps_quote(config.smtp_domain)}' }}",
        "}",
        "Update-EmailAddressPolicy -Identity $policyName",
        "",
        "Remove-PSSession $session",
        "Write-Host \"Politique d'adresses appliquée : $policyName\"",
    ]
    if note:
        lines.insert(4, note)
    return "\n".join(lines) + "\n"


def save_script(content: str, dest: Path) -> Path:
    """Écrit un script PowerShell (UTF-8 **avec BOM** — requis par Windows PowerShell 5.1)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8-sig")
    return dest


def address_report_csv(plans: Sequence[MailboxPlan], dest: Path) -> Path:
    """Rapport des adresses (CSV ';' UTF-8 BOM, convention Excel FR)."""
    lines = ["Identifiant;Type;SMTP principal;Aliases"]
    for plan in plans:
        lines.append(
            f"{plan.sam or plan.cn};{plan.kind};{plan.primary};"
            f"{' | '.join(plan.aliases)}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return dest
