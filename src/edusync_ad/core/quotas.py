"""Quotas de disque FSRM (M14).

Définition d'un quota par OU ou groupe : taille, seuil d'alerte, blocage dur.
Application automatique à la création d'un dossier personnel (M17) via la
génération de commandes FSRM (``New-FsrmQuotaTemplate`` / ``New-FsrmQuota``),
et rapport de quotas exportable en CSV.

Les cmdlets FSRM ne sont exécutables que sur un serveur Windows disposant du
rôle File Server Resource Manager : le script PowerShell généré est déployable
tel quel (voir ``HomeDirsPage`` qui l'applique à la génération M17).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from edusync_ad.core.config import config_dir

QUOTA_CONFIG_FILE = config_dir() / "quota_configs.json"

DEFAULT_SIZE_GB = 10.0
DEFAULT_WARNING_PERCENT = 80
GB = 1024 ** 3


@dataclass
class QuotaSettings:
    """Paramètres d'un quota de dossier personnel."""

    size_gb: float = DEFAULT_SIZE_GB
    warning_percent: int = DEFAULT_WARNING_PERCENT
    hard_limit: bool = True   # blocage dur (sinon quota souple)

    @property
    def size_bytes(self) -> int:
        return int(self.size_gb * GB)

    @property
    def warning_bytes(self) -> int:
        return int(self.size_bytes * self.warning_percent / 100)

    @property
    def template_name(self) -> str:
        """Nom du template FSRM (déduit des paramètres)."""
        size = f"{self.size_gb:g}".replace(".", "p")
        mode = "dur" if self.hard_limit else "souple"
        return f"EduSync-{size}Go-{self.warning_percent}pct-{mode}"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.size_gb <= 0:
            errors.append("La taille du quota doit être strictement positive.")
        if not (0 < self.warning_percent < 100):
            errors.append("Le seuil d'alerte doit être compris entre 1 et 99 %.")
        return errors

    def to_dict(self) -> dict:
        return {
            "size_gb": self.size_gb,
            "warning_percent": self.warning_percent,
            "hard_limit": self.hard_limit,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuotaSettings":
        return cls(
            size_gb=float(data.get("size_gb", DEFAULT_SIZE_GB)),
            warning_percent=int(data.get("warning_percent", DEFAULT_WARNING_PERCENT)),
            hard_limit=bool(data.get("hard_limit", True)),
        )


@dataclass
class QuotaPlan:
    """Quota applicable à un dossier personnel précis."""

    user_dn: str
    sam: str
    home_path: str
    settings: QuotaSettings

    @property
    def template_name(self) -> str:
        return self.settings.template_name

    @property
    def ad_attributes(self) -> dict[str, str]:
        """Pas d'attribut AD propre au quota (FSRM vit côté serveur de fichiers)."""
        return {}


class QuotaManager:
    """Résolution des quotas (priorité groupe > OU parente > défaut) et
    génération des commandes FSRM / rapports CSV."""

    def __init__(
        self,
        ou_configs: dict[str, QuotaSettings] | None = None,
        group_configs: dict[str, QuotaSettings] | None = None,
        default_config: QuotaSettings | None = None,
    ) -> None:
        self.ou_configs: dict[str, QuotaSettings] = ou_configs or {}
        self.group_configs: dict[str, QuotaSettings] = group_configs or {}
        self.default_config: QuotaSettings = default_config or QuotaSettings()

    # -- Résolution -------------------------------------------------------------

    def get_settings_for_user(
        self, ou_dn: str = "", groups: Sequence[str] | None = None
    ) -> QuotaSettings:
        """Priorité : groupe > OU (et ses parentes) > défaut."""
        if groups:
            for group_dn in groups:
                if group_dn in self.group_configs:
                    return self.group_configs[group_dn]

        current = ou_dn
        while current:
            if current in self.ou_configs:
                return self.ou_configs[current]
            parts = current.split(",", 1)
            current = parts[1] if len(parts) > 1 else ""

        return self.default_config

    def plan_for_user(
        self,
        user_dn: str,
        sam: str,
        home_path: str,
        ou_dn: str = "",
        groups: Sequence[str] | None = None,
    ) -> QuotaPlan:
        settings = self.get_settings_for_user(ou_dn, groups)
        return QuotaPlan(
            user_dn=user_dn, sam=sam, home_path=home_path, settings=settings
        )

    def plans_for_homes(
        self,
        home_plans: Iterable,
        ou_dn: str = "",
        groups_by_sam: dict[str, list[str]] | None = None,
    ) -> list[QuotaPlan]:
        """home_plans : objets HomeDirPlan (M17) ou dicts {user_dn, sam, home_path}."""
        groups_by_sam = groups_by_sam or {}
        plans: list[QuotaPlan] = []
        for home in home_plans:
            if hasattr(home, "user_dn"):
                user_dn, sam, path = home.user_dn, home.sam, home.home_path
            else:
                user_dn = home.get("user_dn", home.get("dn", ""))
                sam = home.get("sam", "")
                path = home.get("home_path", "")
            plans.append(
                self.plan_for_user(user_dn, sam, path, ou_dn, groups_by_sam.get(sam))
            )
        return plans

    # -- Script FSRM --------------------------------------------------------------

    def powershell_script(self, plans: Sequence[QuotaPlan]) -> str:
        """Génère le script PowerShell appliquant les quotas FSRM."""
        lines = [
            "#Requires -RunAsAdministrator",
            "<#",
            " Script généré par EduSync-AD — Quotas de disque (M14)",
            f" Quotas : {len(plans)} dossier(s) — rôle FSRM requis sur ce serveur",
            "#>",
            "$ErrorActionPreference = 'Stop'",
            "Import-Module FSRM",
            "",
        ]

        # Templates uniques (déduits des paramètres)
        templates = sorted({plan.template_name for plan in plans})
        for name in templates:
            sample = next(p.settings for p in plans if p.template_name == name)
            threshold = (
                "Hard limit — le dépassement bloque l'écriture"
                if sample.hard_limit
                else "Quota souple — dépassement toléré (journalisé)"
            )
            lines.extend(
                [
                    f"$template = '{name}'",
                    f"# {sample.size_gb:g} Go, alerte à {sample.warning_percent} % — {threshold}",
                    "if (-not (Get-FsrmQuotaTemplate -Name $template -ErrorAction SilentlyContinue)) {",
                    "    New-FsrmQuotaTemplate -Name $template -Size "
                    f"{sample.size_bytes} -Threshold @{{ Usage = {sample.warning_percent}; "
                    "Action = 'EventOnUse' }}",
                    "}",
                    "",
                ]
            )

        for plan in plans:
            soft = "$true" if not plan.settings.hard_limit else "$false"
            lines.extend(
                [
                    f"$path = '{plan.home_path}'",
                    "if (Test-Path -LiteralPath $path) {",
                    "    if (Get-FsrmQuota -Path $path -ErrorAction SilentlyContinue) {",
                    f"        Set-FsrmQuota -Path $path -Size {plan.settings.size_bytes} "
                    f"-Template '{plan.template_name}'",
                    "    } else {",
                    f"        New-FsrmQuota -Path $path -Size {plan.settings.size_bytes} "
                    f"-Template '{plan.template_name}' -SoftLimit:{soft}",
                    "    }",
                    "} else {",
                    f"    Write-Warning \"Dossier absent, quota non appliqué : $path\"",
                    "}",
                    "",
                ]
            )

        lines.append("Write-Host 'Quotas FSRM appliqués.'")
        return "\n".join(lines) + "\n"

    def save_powershell_script(
        self, plans: Sequence[QuotaPlan], dest: Path
    ) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.powershell_script(plans), encoding="utf-8-sig")
        return dest

    # -- Rapport quotas (CSV) --------------------------------------------------------

    @staticmethod
    def report_rows(
        plans: Sequence[QuotaPlan],
        usage_bytes: dict[str, int] | None = None,
    ) -> list[dict]:
        """Rapport : une ligne par quota, avec occupation si connue.

        usage_bytes : {chemin dossier personnel: octets utilisés}
        """
        usage_bytes = usage_bytes or {}
        rows: list[dict] = []
        for plan in plans:
            s = plan.settings
            used = usage_bytes.get(plan.home_path)
            if used is None:
                percent_used: float | str = ""
                remaining: float | str = ""
                state = "Non relevé"
            else:
                percent_used = round(used / s.size_bytes * 100, 1)
                remaining = f"{(s.size_bytes - used) / GB:.2f}"
                if used >= s.size_bytes:
                    state = "Dépassé"
                elif used >= s.warning_bytes:
                    state = "Alerte"
                else:
                    state = "OK"
            rows.append(
                {
                    "identifiant": plan.sam,
                    "chemin": plan.home_path,
                    "quota_go": f"{s.size_gb:g}",
                    "alerte_pct": str(s.warning_percent),
                    "alerte_go": f"{s.warning_bytes / GB:.2f}",
                    "type": "Dur" if s.hard_limit else "Souple",
                    "template": plan.template_name,
                    "utilise_go": f"{used / GB:.2f}" if used is not None else "",
                    "occupation_pct": percent_used,
                    "restant_go": remaining,
                    "etat": state,
                }
            )
        return rows

    @staticmethod
    def export_report_csv(rows: Sequence[dict], dest: Path) -> Path:
        """Export du rapport de quotas (CSV « ; », compatible Excel FR)."""
        columns = [
            "identifiant", "chemin", "quota_go", "alerte_pct", "alerte_go",
            "type", "template", "utilise_go", "occupation_pct", "restant_go", "etat",
        ]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return dest


# -- Persistance ---------------------------------------------------------------

def load_all_quota_configs() -> tuple[
    dict[str, QuotaSettings], dict[str, QuotaSettings], QuotaSettings
]:
    """Charge les configs de quota : OU, groupes, défaut."""
    ou_configs: dict[str, QuotaSettings] = {}
    group_configs: dict[str, QuotaSettings] = {}
    default_config = QuotaSettings()
    if QUOTA_CONFIG_FILE.exists():
        try:
            with QUOTA_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            ou_configs = {
                k: QuotaSettings.from_dict(v)
                for k, v in data.get("ou_configs", {}).items()
            }
            group_configs = {
                k: QuotaSettings.from_dict(v)
                for k, v in data.get("group_configs", {}).items()
            }
            default_config = QuotaSettings.from_dict(data.get("default_config", {}))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return ou_configs, group_configs, default_config


def save_all_quota_configs(
    ou_configs: dict[str, QuotaSettings],
    group_configs: dict[str, QuotaSettings],
    default_config: QuotaSettings,
) -> None:
    QUOTA_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "ou_configs": {k: v.to_dict() for k, v in ou_configs.items()},
        "group_configs": {k: v.to_dict() for k, v in group_configs.items()},
        "default_config": default_config.to_dict(),
    }
    with QUOTA_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
