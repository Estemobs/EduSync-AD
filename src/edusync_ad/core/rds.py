"""M21 — RDS / Bureau à distance : collections, RemoteApp, profils UPD/FSLogix.

Le déploiement RDS (RD Connection Broker) se pilote en **Remote PowerShell
depuis une machine Windows** possédant le module ``RemoteDesktop`` : ce module
génère les scripts correspondants (aucun accès direct possible depuis
EduSync-AD, qui tourne sur Linux/Windows sans module RDS).

Traitements :

- **Collections de sessions** : ``New-RDSessionCollection`` idempotent
  (création si absente, sinon ajout des hébergeurs manquants via
  ``New-RDSessionHost``), groupes autorisés par
  ``Set-RDSessionCollectionConfiguration -UserGroup`` (format ``DOMAINE\Groupe``,
  la liste remplace ``Domain Users`` par défaut — c'est documenté dans le script).
- **UPD** (profil par collection) : même cmdlet avec
  ``-EnableUserProfileDisk -MaxUserProfileDiskSizeGB -DiskPath`` — l'UPD est
  un paramètre **de collection**, pas global.
- **RemoteApp** : ``New-RDRemoteApp`` / ``Set-RDRemoteApp`` idempotents avec
  ``-UserGroups`` = groupes AD autorisés à voir l'application (publication par
  groupe AD).
- **FSLogix** : agent + clés de registre ``HKLM\\SOFTWARE\\FSLogix\\Profiles``
  (``Enabled``, ``VHDLocations``, ``SizeInMB``, ``FlipFlopProfileDir``…),
  script à exécuter sur chaque RD Session Host.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from edusync_ad.core.config import config_dir

RDS_CONFIG_FILE = config_dir() / "rds.json"
RDS_PLANS_FILE = config_dir() / "rds_plans.json"

PROFILE_MODES: dict[str, str] = {
    "none": "Aucun",
    "upd": "UPD — disque de profil par collection",
    "fslogix": "FSLogix — conteneur de profil",
}


@dataclass
class RDSConfig:
    """Paramètres du déploiement RDS."""

    broker: str = ""                    # FQDN du RD Connection Broker
    domain: str = ""                    # domaine NetBIOS pour les groupes (LYCEE\Profs)
    profile_mode: str = "none"           # none | upd | fslogix
    # UPD (paramètres de collection)
    upd_path: str = "\\\\sr01\\UPD$"     # partage UNC des disques de profil
    upd_size_gb: int = 30
    upd_include_paths: list[str] = field(default_factory=list)
    upd_exclude_paths: list[str] = field(default_factory=list)
    # FSLogix (registre des RD Session Host)
    fslogix_locations: list[str] = field(default_factory=list)   # UNC (VHDLocations)
    fslogix_size_mb: int = 30000
    fslogix_flipflop: bool = True        # dossier profil = nom utilisateur (pas SID)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.broker.strip():
            errors.append("Le Connection Broker est vide.")
        elif " " in self.broker:
            errors.append("Le Connection Broker doit être un FQDN (sans espace).")
        if self.profile_mode not in PROFILE_MODES:
            errors.append("Mode de profil inconnu (none / upd / fslogix).")
        if self.profile_mode == "upd":
            if not self.upd_path.strip():
                errors.append("Chemin UPD vide.")
            elif not self.upd_path.startswith("\\\\"):
                errors.append("Le chemin UPD doit être un partage UNC (\\\\serveur\\partage).")
            if self.upd_size_gb <= 0:
                errors.append("La taille UPD doit être supérieure à 0 Go.")
        if self.profile_mode == "fslogix":
            if not self.fslogix_locations:
                errors.append("Aucune localisation VHD FSLogix (VHDLocations).")
            for loc in self.fslogix_locations:
                if not loc.startswith("\\\\"):
                    errors.append(f"Localisation FSLogix non-UNC : {loc}")
            if self.fslogix_size_mb <= 0:
                errors.append("La taille de profil FSLogix doit être supérieure à 0 Mo.")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RDSConfig":
        cfg = cls()
        for key in (
            "broker", "domain", "profile_mode", "upd_path", "upd_size_gb",
            "upd_include_paths", "upd_exclude_paths",
            "fslogix_locations", "fslogix_size_mb", "fslogix_flipflop",
        ):
            if key in data:
                setattr(cfg, key, data[key])
        return cfg


def load_rds_config() -> RDSConfig:
    if RDS_CONFIG_FILE.exists():
        try:
            with RDS_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                return RDSConfig.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError):
            pass
    return RDSConfig()


def save_rds_config(config: RDSConfig) -> None:
    RDS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RDS_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)


# -- Utilitaires ------------------------------------------------------------------------------


def split_multi(text: str) -> list[str]:
    """ découpe « a ; b, c » en liste nettoyée et dédoublonnée (ordre conservé)."""
    parts: list[str] = []
    for raw in re.split(r"[;,]", text or ""):
        item = raw.strip()
        if item and item not in parts:
            parts.append(item)
    return parts


def normalize_group_ref(group: str, domain: str = "") -> str:
    """Format ``DOMAINE\Groupe`` attendu par RDS (``\\`` déjà présent = inchangé)."""
    group = (group or "").strip()
    if not group:
        return ""
    if "\\" in group:
        return group
    domain = (domain or "").strip().rstrip("\\")
    return f"{domain}\\{group}" if domain else group


def make_alias(text: str) -> str:
    """Alias RemoteApp à partir d'un nom : minuscules, ``[a-z0-9._-]``."""
    alias = (text or "").strip().lower()
    alias = re.sub(r"[^a-z0-9._-]+", "-", alias)
    alias = re.sub(r"-{2,}", "-", alias)
    return alias.strip("-._")


# -- Plans ------------------------------------------------------------------------------------


@dataclass
class CollectionPlan:
    """Collection de sessions RDS à créer/configurer."""

    name: str = ""
    description: str = ""
    session_hosts: list[str] = field(default_factory=list)
    user_groups: list[str] = field(default_factory=list)   # déjà normalisés DOMAINE\g

    @property
    def label(self) -> str:
        groups = f", {len(self.user_groups)} groupe(s)" if self.user_groups else ""
        return f"{self.name} — {len(self.session_hosts)} hôte(s){groups}"


@dataclass
class RemoteAppPlan:
    """Application RemoteApp à publier."""

    alias: str = ""
    display_name: str = ""
    file_path: str = ""
    collection: str = ""
    user_groups: list[str] = field(default_factory=list)   # déjà normalisés
    show_in_web: bool = True
    folder_name: str = ""
    icon_index: int | None = None
    required_command_line: str = ""

    @property
    def label(self) -> str:
        groups = f" → {len(self.user_groups)} groupe(s)" if self.user_groups else ""
        return f"{self.alias} ({self.display_name}) → {self.collection}{groups}"


def build_collection_plan(
    name: str,
    description: str,
    session_hosts: Sequence[str],
    user_groups: Sequence[str],
    config: RDSConfig,
) -> CollectionPlan:
    hosts: list[str] = []
    for entry in session_hosts:
        for host in split_multi(entry):
            host = host.strip().rstrip(".")
            if host and host not in hosts:
                hosts.append(host)
    groups: list[str] = []
    for entry in user_groups:
        for group in split_multi(entry):
            normalized = normalize_group_ref(group, config.domain)
            if normalized and normalized not in groups:
                groups.append(normalized)
    return CollectionPlan(
        name=name.strip(), description=description.strip(),
        session_hosts=hosts, user_groups=groups,
    )


def validate_collection(plan: CollectionPlan, config: RDSConfig) -> list[str]:
    errors: list[str] = []
    if not plan.name.strip():
        errors.append("Nom de collection vide.")
    if not plan.session_hosts:
        errors.append(f"Collection « {plan.name or '?'} » : au moins un hébergeur requis.")
    if not config.broker.strip():
        errors.append("Connection Broker vide.")
    return errors


def validate_remoteapp(plan: RemoteAppPlan) -> list[str]:
    errors: list[str] = []
    if not plan.alias.strip():
        errors.append("Alias RemoteApp vide.")
    elif not re.match(r"^[A-Za-z0-9._-]+$", plan.alias):
        errors.append(
            f"Alias « {plan.alias} » invalide (lettres, chiffres, . _ - seulement)."
        )
    if not plan.display_name.strip():
        errors.append("Nom d'affichage vide.")
    if not plan.file_path.strip():
        errors.append("Chemin de l'exécutable vide.")
    if not plan.collection.strip():
        errors.append(f"RemoteApp « {plan.alias or '?'} » : collection non précisée.")
    return errors


# -- Persistance des plans ------------------------------------------------------------------------

def save_rds_plans(
    collections: Sequence[CollectionPlan],
    remoteapps: Sequence[RemoteAppPlan],
) -> None:
    RDS_PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RDS_PLANS_FILE.open("w", encoding="utf-8") as fh:
        json.dump(
            {"collections": [asdict(c) for c in collections],
             "remoteapps": [asdict(a) for a in remoteapps]},
            fh, indent=2, ensure_ascii=False,
        )


def load_rds_plans() -> tuple[list[CollectionPlan], list[RemoteAppPlan]]:
    if not RDS_PLANS_FILE.exists():
        return [], []
    try:
        with RDS_PLANS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        collections = [
            CollectionPlan(**{k: v for k, v in item.items() if k in CollectionPlan.__dataclass_fields__})
            for item in data.get("collections", [])
        ]
        remoteapps = [
            RemoteAppPlan(**{k: v for k, v in item.items() if k in RemoteAppPlan.__dataclass_fields__})
            for item in data.get("remoteapps", [])
        ]
        return collections, remoteapps
    except (OSError, ValueError, TypeError, AttributeError):
        return [], []


# -- Scripts PowerShell ---------------------------------------------------------------------------


def _q(value: str) -> str:
    return (value or "").replace("'", "''")


def _ps_array(values: Sequence[str]) -> str:
    return "@(" + ", ".join(f"'{_q(v)}'" for v in values) + ")"


def collection_script(
    plans: Sequence[CollectionPlan], config: RDSConfig
) -> str:
    """Script de création/configuration des collections (+ UPD si mode « upd »)."""
    errors = config.validate()
    if errors:
        raise ValueError("; ".join(errors))
    if not plans:
        raise ValueError("Aucune collection à générer.")
    for plan in plans:
        bad = validate_collection(plan, config)
        if bad:
            raise ValueError("; ".join(bad))

    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Collections RDS (M21)",
        f" Broker : {config.broker}",
        f" Collections : {len(plans)} — profils : {PROFILE_MODES[config.profile_mode]}",
        " Module requis : RemoteDesktop (tools de gestion RDS / RSAT-RDS).",
        " S'exécute sur un serveur Windows disposant du module (broker ou admin).",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        f"$broker = '{_q(config.broker)}'",
        "",
    ]

    for plan in plans:
        lines.extend([
            "# --- collection : " + plan.name + " ---",
            "$collectionName = '" + _q(plan.name) + "'",
            "$existing = Get-RDSessionCollection -ConnectionBroker $broker "
            "-ErrorAction SilentlyContinue | "
            "Where-Object { $_.CollectionName -eq $collectionName }",
            "if (-not $existing) {",
            "    New-RDSessionCollection -CollectionName $collectionName"
            + (f" -CollectionDescription '{_q(plan.description)}'" if plan.description else "")
            + f" -SessionHost {_ps_array(plan.session_hosts)} -ConnectionBroker $broker",
            "} else {",
            "    # collection existante : ajout des hébergeurs manquants",
            "    $hosts = @(Get-RDSessionHost -CollectionName $collectionName "
            "-ConnectionBroker $broker -ErrorAction SilentlyContinue | "
            "ForEach-Object { $_.SessionHost })",
            "    foreach ($h in " + _ps_array(plan.session_hosts) + ") {",
            "        if ($hosts -notcontains $h) {",
            "            New-RDSessionHost -CollectionName $collectionName "
            "-SessionHost $h -ConnectionBroker $broker",
            "        }",
            "    }",
            "}",
        ])
        if plan.user_groups:
            lines.extend([
                "# groupes autorisés (la liste REMPLACE 'Domain Users' par défaut :",
                "# inclure 'DOMAINE\\Domain Users' pour le conserver)",
                "Set-RDSessionCollectionConfiguration -CollectionName $collectionName "
                f"-UserGroup {_ps_array(plan.user_groups)} -ConnectionBroker $broker",
            ])
        if config.profile_mode == "upd":
            lines.append("# disque de profil (UPD) de la collection")
            upd = [
                "Set-RDSessionCollectionConfiguration -CollectionName $collectionName `",
                "    -EnableUserProfileDisk `",
                f"    -MaxUserProfileDiskSizeGB {config.upd_size_gb} `",
                f"    -DiskPath '{_q(config.upd_path)}' `",
            ]
            if config.upd_include_paths:
                upd.append(f"    -IncludeFolderPath {_ps_array(config.upd_include_paths)} `")
            if config.upd_exclude_paths:
                upd.append(f"    -ExcludeFolderPath {_ps_array(config.upd_exclude_paths)} `")
            upd.append("    -ConnectionBroker $broker")
            lines.extend(upd)
        lines.append("")

    lines.append("Write-Host 'Collections RDS configurées.'")
    return "\n".join(lines) + "\n"


def remoteapp_script(
    plans: Sequence[RemoteAppPlan], config: RDSConfig
) -> str:
    """Script de publication des RemoteApp (idempotent, filtrage par groupes)."""
    if not config.broker.strip():
        raise ValueError("Connection Broker vide.")
    if not plans:
        raise ValueError("Aucun RemoteApp à générer.")
    for plan in plans:
        bad = validate_remoteapp(plan)
        if bad:
            raise ValueError("; ".join(bad))

    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Applications RemoteApp (M21)",
        f" Broker : {config.broker} — applications : {len(plans)}",
        " -UserGroups limite l'affichage de l'app dans RD Web Access aux groupes listés.",
        " App existante → mise à jour (Set-RDRemoteApp), sinon création.",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        f"$broker = '{_q(config.broker)}'",
        "",
    ]

    for plan in plans:
        app_lines = [
            "    New-RDRemoteApp -CollectionName $collectionName -Alias $alias "
            f"-DisplayName '{_q(plan.display_name)}' -FilePath '{_q(plan.file_path)}' "
            f"-ShowInWebAccess {'$true' if plan.show_in_web else '$false'} "
            + (f"-FolderName '{_q(plan.folder_name)}' " if plan.folder_name else "")
            + (f"-IconIndex {plan.icon_index} " if plan.icon_index is not None else "")
            + (f"-UserGroups {_ps_array(plan.user_groups)} " if plan.user_groups else "")
            + ("-CommandLineSetting AllowOnlySpecifiedCommandLine "
               f"-RequiredCommandLine '{_q(plan.required_command_line)}' "
               if plan.required_command_line else "")
            + "-ConnectionBroker $broker",
        ]
        set_lines = [
            "    Set-RDRemoteApp -CollectionName $collectionName -Alias $alias "
            f"-DisplayName '{_q(plan.display_name)}' -FilePath '{_q(plan.file_path)}' "
            f"-ShowInWebAccess {'$true' if plan.show_in_web else '$false'} "
            + (f"-FolderName '{_q(plan.folder_name)}' " if plan.folder_name else "")
            + (f"-IconIndex {plan.icon_index} " if plan.icon_index is not None else "")
            + (f"-UserGroups {_ps_array(plan.user_groups)} " if plan.user_groups else "")
            + "-ConnectionBroker $broker",
        ]
        lines.extend([
            "# --- " + plan.alias + " (" + plan.display_name + ") ---",
            "$collectionName = '" + _q(plan.collection) + "'",
            "$alias = '" + _q(plan.alias) + "'",
            "$existing = Get-RDRemoteApp -CollectionName $collectionName "
            "-ConnectionBroker $broker -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Alias -eq $alias }",
            "if (-not $existing) {",
            *app_lines,
            "} else {",
            *set_lines,
            "}",
            "",
        ])

    lines.append("Write-Host 'RemoteApp publiés.'")
    return "\n".join(lines) + "\n"


def save_script(content: str, dest: Path) -> Path:
    """Écrit un script PowerShell (UTF-8 **avec BOM** — requis par Windows PowerShell 5.1)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8-sig")
    return dest


def fslogix_script(config: RDSConfig) -> str:
    """Script de configuration FSLogix (clés de registre, à lancer sur chaque hôte)."""
    if config.profile_mode != "fslogix":
        raise ValueError("Le mode de profil sélectionné n'est pas FSLogix.")
    if not config.fslogix_locations:
        raise ValueError("Aucune localisation VHD FSLogix.")
    for loc in config.fslogix_locations:
        if not loc.startswith("\\\\"):
            raise ValueError(f"Localisation FSLogix non-UNC : {loc}")
    if config.fslogix_size_mb <= 0:
        raise ValueError("La taille de profil FSLogix doit être supérieure à 0 Mo.")

    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Profils FSLogix (M21)",
        f" Conteneurs : {len(config.fslogix_locations)} partage(s), "
        f"taille {config.fslogix_size_mb} Mo",
        " À exécuter sur CHAQUE RD Session Host (agent FSLogix installé,",
        " redémarrage requis après application).",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        "$path = 'HKLM:\\SOFTWARE\\FSLogix\\Profiles'",
        "if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }",
        "Set-ItemProperty -Path $path -Name 'Enabled' -Value 1 -Type DWord",
        "Set-ItemProperty -Path $path -Name 'VHDLocations' -Value "
        + _ps_array(config.fslogix_locations) + " -Type MultiString",
        f"Set-ItemProperty -Path $path -Name 'SizeInMB' -Value {config.fslogix_size_mb} -Type DWord",
        # profil = nom d'utilisateur (pas SID-xxxxxxxx) : arborescence lisible
        "Set-ItemProperty -Path $path -Name 'DeleteLocalProfileWhenVHDShouldApply' "
        "-Value 1 -Type DWord",
    ]
    if config.fslogix_flipflop:
        lines.append(
            "Set-ItemProperty -Path $path -Name 'FlipFlopProfileDir' -Value 1 -Type DWord"
        )
    lines.extend([
        "Write-Host 'FSLogix configuré (redémarrage des RD Session Host requis).'",
    ])
    return "\n".join(lines) + "\n"
