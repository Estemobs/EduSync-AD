"""Dossiers personnels / Home Directory (M17).

Création auto à la création de compte : ``\\\\srv\\homes\\%USERNAME%``.
Attributs AD : ``homeDirectory`` (chemin UNC) + ``homeDrive`` (lettre, ex H:).
Droits NTFS : utilisateur (Modification), Administrateurs (Contrôle total),
SYSTEM (Contrôle total) — appliqués via ``icacls`` / PowerShell sur le serveur
de fichiers (générés ici, exécutables à distance ou localement sous Windows).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from edusync_ad.core.config import config_dir

HOME_CONFIG_FILE = config_dir() / "home_dir_config.json"

DEFAULT_SHARE_ROOT = r"\\srv\homes"
DEFAULT_DRIVE_LETTER = "H:"
DEFAULT_FOLDER_TEMPLATE = "%USERNAME%"

# Droits NTFS par principal (icacls : M = Modify, F = Full control)
ACL_USER_RIGHTS = "M"
ACL_ADMINS_RIGHTS = "F"
ACL_SYSTEM_RIGHTS = "F"


@dataclass
class HomeDirConfig:
    """Configuration des dossiers personnels."""

    share_root: str = DEFAULT_SHARE_ROOT          # ex : \\srv\homes
    drive_letter: str = DEFAULT_DRIVE_LETTER      # ex : H:
    folder_template: str = DEFAULT_FOLDER_TEMPLATE  # %USERNAME% | %SAM% | %OU%
    apply_on_create: bool = True                  # appliquer à la création de compte

    # Variables supportées : %USERNAME%, %SAM%, %OU%, %DOMAIN%
    def resolve(self, username: str, sam: str, ou_dn: str = "", domain: str = "") -> str:
        """Résout le chemin UNC du dossier personnel."""
        ou_name = ""
        if ou_dn:
            for part in ou_dn.split(","):
                if part.strip().upper().startswith("OU="):
                    ou_name = part.split("=", 1)[1]
                    break
        folder = self.folder_template
        for var, value in (
            ("%USERNAME%", username),
            ("%SAM%", sam),
            ("%OU%", ou_name),
            ("%DOMAIN%", domain),
        ):
            folder = folder.replace(var, value)
        root = self.share_root.rstrip("\\")
        return f"{root}\\{folder}"

    def validate(self) -> list[str]:
        """Retourne la liste des erreurs de configuration (vide = valide)."""
        errors: list[str] = []
        if not self.share_root.strip():
            errors.append("Le partage racine est vide.")
        elif not self.share_root.startswith("\\\\"):
            errors.append("Le partage racine doit être un chemin UNC (\\\\serveur\\partage).")
        if len(self.drive_letter.strip()) != 2 or not self.drive_letter.strip().upper().endswith(":"):
            errors.append("La lettre de lecteur doit être de la forme « H: ».")
        if not self.folder_template.strip():
            errors.append("Le modèle de dossier est vide.")
        return errors

    def to_dict(self) -> dict:
        return {
            "share_root": self.share_root,
            "drive_letter": self.drive_letter,
            "folder_template": self.folder_template,
            "apply_on_create": self.apply_on_create,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HomeDirConfig":
        return cls(
            share_root=data.get("share_root", DEFAULT_SHARE_ROOT),
            drive_letter=data.get("drive_letter", DEFAULT_DRIVE_LETTER),
            folder_template=data.get("folder_template", DEFAULT_FOLDER_TEMPLATE),
            apply_on_create=bool(data.get("apply_on_create", True)),
        )


@dataclass
class HomeDirPlan:
    """Plan d'application pour un compte : attributs AD + chemin + ACL."""

    user_dn: str
    sam: str
    username: str
    home_path: str
    drive_letter: str

    @property
    def ad_attributes(self) -> dict[str, str]:
        return {"homeDirectory": self.home_path, "homeDrive": self.drive_letter}


class HomeDirManager:
    """Génère attributs AD, commandes NTFS et scripts PowerShell pour les homes."""

    def __init__(self, config: HomeDirConfig | None = None) -> None:
        self.config = config or HomeDirConfig()

    # -- Plans ----------------------------------------------------------------

    def plan_for_user(
        self, user_dn: str, username: str, sam: str, ou_dn: str = "", domain: str = ""
    ) -> HomeDirPlan:
        path = self.config.resolve(username, sam, ou_dn, domain)
        return HomeDirPlan(
            user_dn=user_dn,
            sam=sam,
            username=username,
            home_path=path,
            drive_letter=self.config.drive_letter,
        )

    def plan_batch(
        self,
        users: Iterable[dict],
        ou_dn: str = "",
        domain: str = "",
    ) -> list[HomeDirPlan]:
        """users : dicts avec au minimum ``dn``, ``sam``, ``cn``."""
        plans: list[HomeDirPlan] = []
        for user in users:
            plans.append(
                self.plan_for_user(
                    user.get("dn", ""),
                    user.get("cn") or user.get("sam", ""),
                    user.get("sam", ""),
                    ou_dn,
                    domain,
                )
            )
        return plans

    # -- Droits NTFS ----------------------------------------------------------

    def icacls_command(
        self,
        home_path: str,
        user_account: str,
        *,
        admins_principal: str = r"BUILTIN\Administrators",
        system_principal: str = "SYSTEM",
    ) -> list[str]:
        """Commande ``icacls`` appliquant les droits M17 sur un dossier.

        Droits : utilisateur (Modification, hérités), Administrateurs et
        SYSTEM (Contrôle total). L'héritance est conservée pour que les
        sous-dossiers créés par l'utilisateur héritent des droits.
        """
        return [
            "icacls",
            home_path,
            "/grant:r",
            f"{user_account}:(OI)(CI){ACL_USER_RIGHTS}",
            f"{admins_principal}:(OI)(CI){ACL_ADMINS_RIGHTS}",
            f"{system_principal}:(OI)(CI){ACL_SYSTEM_RIGHTS}",
        ]

    def acl_lines(
        self,
        plans: Sequence[HomeDirPlan],
        domain: str = "",
    ) -> list[list[str]]:
        """Commandes icacls (une par compte) avec compte ``DOMAIN\\sam``."""
        prefix = f"{domain}\\" if domain else ""
        return [
            self.icacls_command(plan.home_path, f"{prefix}{plan.sam}") for plan in plans
        ]

    # -- Script PowerShell ----------------------------------------------------

    def powershell_script(
        self,
        plans: Sequence[HomeDirPlan],
        *,
        domain: str = "",
        create_quota_note: bool = True,
    ) -> str:
        """Génère un script PowerShell : création des dossiers + droits NTFS."""
        lines = [
            "#Requires -RunAsAdministrator",
            "<#",
            " Script généré par EduSync-AD — Dossiers personnels (M17)",
            f" Dossiers : {len(plans)}",
            " Droits : utilisateur (Modification), Administrateurs et SYSTEM (Contrôle total)",
            "#>",
            "$ErrorActionPreference = 'Stop'",
            "",
        ]
        prefix = f"{domain}\\" if domain else ""
        for plan in plans:
            lines.extend(
                [
                    f"$path = '{plan.home_path}'",
                    "if (-not (Test-Path -LiteralPath $path)) {",
                    "    New-Item -Path $path -ItemType Directory -Force | Out-Null",
                    "}",
                    "$acl = Get-Acl -LiteralPath $path",
                    "$acl.SetAccessRuleProtection($false, $true)",
                    f"$user = New-Object System.Security.Principal.NTAccount('{prefix}{plan.sam}')",
                    f"$admins = New-Object System.Security.Principal.NTAccount('BUILTIN\\Administrators')",
                    f"$system = New-Object System.Security.Principal.NTAccount('SYSTEM')",
                    "foreach ($pair in @(",
                    "    @{ Principal = $user;  Rights = 'Modify' },",
                    "    @{ Principal = $admins; Rights = 'FullControl' },",
                    "    @{ Principal = $system; Rights = 'FullControl' }",
                    ")) {",
                    "    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(",
                    "        $pair.Principal, $pair.Right,",
                    "        'ContainerInherit,ObjectInherit', 'None', 'Allow')",
                    "    $acl.AddAccessRule($rule)",
                    "}",
                    "Set-Acl -LiteralPath $path -AclObject $acl",
                    "",
                ]
            )
        if create_quota_note:
            lines.append(
                "# Quotas FSRM : voir M14 — appliquer un quota sur "
                f"{self.config.share_root}"
            )
        lines.append("Write-Host 'Dossiers personnels créés / droits appliqués.'")
        return "\n".join(lines) + "\n"

    def save_powershell_script(
        self, plans: Sequence[HomeDirPlan], dest: Path, *, domain: str = ""
    ) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.powershell_script(plans, domain=domain), encoding="utf-8-sig")
        return dest

    # -- Exécution locale (Windows + accès direct au partage) ----------------

    def create_folder(self, home_path: str) -> bool:
        """Crée le dossier si le chemin est accessible (UNC ou local).

        Retourne False si le chemin n'est pas créable depuis cette machine
        (cas typique : l'application tourne hors serveur de fichiers) — le
        script PowerShell généré reste alors la voie recommandée.
        """
        if shutil.which("icacls") is None and not home_path.startswith("\\\\"):
            return False
        try:
            Path(home_path).mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def apply_acl(self, home_path: str, user_account: str, domain: str = "") -> bool:
        """Applique les droits NTFS localement via icacls (Windows)."""
        cmd = self.icacls_command(home_path, user_account)
        if shutil.which("icacls") is None:
            return False
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


# -- Persistance -------------------------------------------------------------

def load_home_dir_config() -> HomeDirConfig:
    if HOME_CONFIG_FILE.exists():
        try:
            with HOME_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                return HomeDirConfig.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError):
            pass
    return HomeDirConfig()


def save_home_dir_config(config: HomeDirConfig) -> None:
    HOME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HOME_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)
