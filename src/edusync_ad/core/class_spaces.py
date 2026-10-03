"""Espaces partagés par classe / groupe (M18).

Création d'un espace SMB + droits NTFS à la création du groupe de classe,
selon le modèle ``\\\\srv\\classes\\%GROUP%`` :

- **Profs** — Lecture/Écriture (``Modify``)
- **Élèves** — Lecture (``ReadAndExecute``)
- **Administrateurs / SYSTEM** — Contrôle total (``FullControl``)

Synchronisation membres AD ↔ droits partage : la comparaison est calculée en
Python (état enregistré ↔ appartenance AD actuelle) et le script PowerShell
généré rejoue les ACE côté serveur de fichiers en interrogeant AD en direct
(``Get-ADGroupMember``), retrait des utilisateurs plus membres.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from edusync_ad.core.config import config_dir

CLASS_SPACE_CONFIG_FILE = config_dir() / "class_spaces.json"
CLASS_SPACE_STATE_FILE = config_dir() / "class_spaces_state.json"

DEFAULT_SHARE_ROOT = r"\\srv\classes"
DEFAULT_FOLDER_TEMPLATE = "%GROUP%"

# Droits NTFS par rôle (valeurs icacls)
RIGHTS_TEACHERS = "Modify"            # profs : Lecture/Écriture
RIGHTS_STUDENTS = "ReadAndExecute"    # élèves : Lecture
RIGHTS_ADMINS = "FullControl"         # admins : Contrôle total

ADMIN_PRINCIPALS = ("BUILTIN\\Administrators", "NT AUTHORITY\\SYSTEM")


@dataclass
class ClassSpaceConfig:
    """Configuration des espaces de classe."""

    share_root: str = DEFAULT_SHARE_ROOT     # ex : \\srv\classes
    folder_template: str = DEFAULT_FOLDER_TEMPLATE  # %GROUP%
    teachers_group: str = ""                 # DN du groupe AD des profs (optionnel)
    teachers_ou: str = ""                    # DN de l'OU des profs (optionnel)
    auto_on_group_create: bool = True        # enregistrer l'espace à la création du groupe

    def resolve_path(self, group_name: str) -> str:
        root = self.share_root.rstrip("\\")
        folder = self.folder_template.replace("%GROUP%", group_name)
        return f"{root}\\{folder}"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.share_root.strip():
            errors.append("Le partage racine est vide.")
        elif not self.share_root.startswith("\\\\"):
            errors.append("Le partage racine doit être un chemin UNC (\\\\serveur\\partage).")
        if not self.folder_template.strip():
            errors.append("Le modèle de dossier est vide.")
        elif "%GROUP%" not in self.folder_template:
            errors.append("Le modèle de dossier doit contenir %GROUP%.")
        return errors

    def to_dict(self) -> dict:
        return {
            "share_root": self.share_root,
            "folder_template": self.folder_template,
            "teachers_group": self.teachers_group,
            "teachers_ou": self.teachers_ou,
            "auto_on_group_create": self.auto_on_group_create,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClassSpaceConfig":
        return cls(
            share_root=data.get("share_root", DEFAULT_SHARE_ROOT),
            folder_template=data.get("folder_template", DEFAULT_FOLDER_TEMPLATE),
            teachers_group=data.get("teachers_group", ""),
            teachers_ou=data.get("teachers_ou", ""),
            auto_on_group_create=bool(data.get("auto_on_group_create", True)),
        )


@dataclass
class ClassSpacePlan:
    """Espace à créer/synchroniser pour un groupe."""

    group_dn: str
    group_name: str
    path: str
    teachers: list[str] = field(default_factory=list)   # sAMAccountName
    students: list[str] = field(default_factory=list)   # sAMAccountName

    @property
    def share_name(self) -> str:
        return self.path.rstrip("\\").split("\\")[-1]

    @property
    def members(self) -> list[str]:
        return [*self.teachers, *self.students]


def classify_member(
    member_dn: str,
    member_groups: Sequence[str],
    config: ClassSpaceConfig,
) -> str:
    """Rôle d'un membre : « prof » ou « eleve »."""
    if config.teachers_group and config.teachers_group in member_groups:
        return "prof"
    if config.teachers_ou:
        ou = config.teachers_ou.strip().lower()
        if member_dn.strip().lower().endswith("," + ou):
            return "prof"
    return "eleve"


class ClassSpaceManager:
    """Construit les plans d'espaces (chemin + classement profs/élèves)."""

    def __init__(self, config: ClassSpaceConfig | None = None, connection=None) -> None:
        self.config = config if config is not None else load_class_space_config()
        self._ad = connection

    def classify(self, member_dn: str, member_groups: Sequence[str] | None = None) -> str:
        """Classe un membre ; interroge l'AD si les groupes ne sont pas fournis."""
        if member_groups is None and self._ad is not None:
            from edusync_ad.core.ad.connection import ADConnection

            try:
                base_dn = ADConnection.domain_to_base_dn(self._ad.domain or "")
                member_groups = self._ad.search_user_groups(member_dn, base_dn)
            except Exception:  # noqa: BLE001 — classement par défaut « eleve »
                member_groups = []
        return classify_member(member_dn, member_groups or [], self.config)

    def plan_for_group(
        self,
        group_dn: str,
        group_name: str,
        members: Sequence = (),
    ) -> ClassSpacePlan:
        """members : dicts {dn, sam} ou DN simples."""
        teachers: list[str] = []
        students: list[str] = []
        for member in members:
            if isinstance(member, dict):
                dn, sam = member.get("dn", ""), member.get("sam", "")
            else:
                dn, sam = str(member), str(member)
            if not dn:
                continue
            bucket = teachers if self.classify(dn) == "prof" else students
            bucket.append(sam or dn)
        return ClassSpacePlan(
            group_dn=group_dn,
            group_name=group_name,
            path=self.config.resolve_path(group_name),
            teachers=teachers,
            students=students,
        )


# -- Diff de synchronisation ---------------------------------------------------------

def plan_sync(
    previous_members: Sequence[str], current_members: Sequence[str]
) -> dict[str, list[str]]:
    """Écart entre l'état enregistré et l'appartenance AD actuelle."""
    prev, curr = set(previous_members), set(current_members)
    return {
        "added": sorted(curr - prev),
        "removed": sorted(prev - curr),
        "unchanged": sorted(prev & curr),
    }


# -- Scripts PowerShell ---------------------------------------------------------------

def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def creation_script(
    plans: Sequence[ClassSpacePlan],
    domain: str = "",
    *,
    teachers_group: str = "",
) -> str:
    """Script PowerShell : dossier + partage SMB + droits NTFS (rôles)."""
    prefix = f"{domain}\\" if domain else ""
    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Espaces partagés par classe (M18)",
        f" Espaces : {len(plans)}",
        " Droits : profs Modification, élèves Lecture, Administrateurs/SYSTEM Total",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for plan in plans:
        path = _ps_quote(plan.path)
        share = _ps_quote(plan.share_name)
        lines.extend(
            [
                f"$path = '{path}'",
                "if (-not (Test-Path -LiteralPath $path)) {",
                "    New-Item -Path $path -ItemType Directory -Force | Out-Null",
                "}",
                f"if (-not (Get-SmbShare -Name '{share}' -ErrorAction SilentlyContinue)) {{",
                f"    New-SmbShare -Name '{share}' -Path $path "
                f"-FullAccess '{ADMIN_PRINCIPALS[0]}' | Out-Null",
                "}",
                # Droits de base
                f"icacls $path /grant:r '{ADMIN_PRINCIPALS[0]}:(OI)(CI){RIGHTS_ADMINS}' "
                f"'{ADMIN_PRINCIPALS[1]}:(OI)(CI){RIGHTS_ADMINS}' /Q",
            ]
        )
        if plan.teachers or plan.students:
            assignments = (
                [(sam, RIGHTS_TEACHERS) for sam in plan.teachers]
                + [(sam, RIGHTS_STUDENTS) for sam in plan.students]
            )
            for sam, rights in assignments:
                principal = f"{prefix}{sam}" if prefix else sam
                lines.append(
                    f"icacls $path /grant:r '{principal}:(OI)(CI){rights}' /Q"
                )
        lines.append("")
    lines.append("Write-Host 'Espaces de classe créés / droits appliqués.'")
    return "\n".join(lines) + "\n"


def sync_script(
    plan: ClassSpacePlan,
    domain: str = "",
    *,
    teachers_group: str = "",
    teachers_ou: str = "",
) -> str:
    """Script PowerShell de synchronisation membres AD ↔ droits NTFS.

    Interroge AD en direct (``Get-ADGroupMember``), classe prof/élève puis
    reconstruit les ACE explicites du dossier : les utilisateurs qui ne sont
    plus membres du groupe perdent leurs droits, les nouveaux membres
    reçoivent leur droit (Modification pour les profs, Lecture pour les élèves).
    Les ACE hérités et ceux des comptes d'administration ne sont jamais touchés.
    """
    path = _ps_quote(plan.path)
    group_id = _ps_quote(plan.group_dn or plan.group_name)
    tg = _ps_quote(teachers_group)
    tou = _ps_quote(teachers_ou.lower())

    lines = [
        "#Requires -RunAsAdministrator",
        "<#",
        " Script généré par EduSync-AD — Synchronisation AD ↔ droits NTFS (M18)",
        f" Groupe : {plan.group_name}",
        f" Espace : {plan.path}",
        " Membres AD : profs Modification, élèves Lecture ;",
        " les utilisateurs retirés du groupe perdent leurs droits explicites.",
        "#>",
        "$ErrorActionPreference = 'Stop'",
        "Import-Module ActiveDirectory",
        f"$path = '{path}'",
        "if (-not (Test-Path -LiteralPath $path)) {",
        "    New-Item -Path $path -ItemType Directory -Force | Out-Null",
        "}",
        "",
        "# 1. Membres actuels du groupe AD",
        f"$group = '{group_id}'",
        "$members = @(Get-ADGroupMember -Identity $group -Recursive | "
        "Where-Object { $_.objectClass -eq 'user' })",
        "",
        "# 2. Classement profs / élèves → droits attendus par SID",
        "$desired = @{}",
        f"$teachersGroup = '{tg}'" if tg else "$teachersGroup = $null",
        f"$teachersOU = '{tou}'" if tou else "$teachersOU = $null",
        "$teacherSids = @()",
        "if ($teachersGroup) {",
        "    $teacherSids = @(Get-ADGroupMember -Identity $teachersGroup -Recursive "
        "| Select-Object -ExpandProperty SID | ForEach-Object { $_.Value })",
        "}",
        "foreach ($m in $members) {",
        "    $isTeacher = $teacherSids -contains $m.SID.Value",
        "    if (-not $isTeacher -and $teachersOU) {",
        "        $isTeacher = $m.DistinguishedName -like ('*' + $teachersOU)",
        "    }",
        "    $rights = if ($isTeacher) { 'Modify' } else { 'ReadAndExecute' }",
        "    $desired[$m.SID.Value] = $rights",
        "}",
        "",
        "# 3. Comptes protégés (SID ou nom) : jamais modifiés",
        "$protectedSids = @('S-1-5-18', 'S-1-5-32-544')",
        "if ($teachersGroup) { $protectedSids += (Get-ADGroup -Identity "
        "$teachersGroup).SID.Value }",
        "",
        "# 4. Reconstruction des ACE explicites (hérités intacts)",
        "$acl = Get-Acl -LiteralPath $path",
        "foreach ($rule in @($acl.Access)) {",
        "    if ($rule.IsInherited) { continue }",
        "    $sid = $null",
        "    try {",
        "        $sid = $rule.IdentityReference.Translate("
        "[System.Security.Principal.SecurityIdentifier]).Value",
        "    } catch { continue }",
        "    if ($protectedSids -contains $sid) { continue }",
        "    if ($rule.IdentityReference.Value -match 'Admin|SYSTEM|CREATOR OWNER') "
        "{ continue }",
        "    $acl.RemoveAccessRule($rule) | Out-Null",
        "}",
        "foreach ($sid in $desired.Keys) {",
        "    $ace = New-Object System.Security.AccessControl.FileSystemAccessRule(",
        "        ([System.Security.Principal.SecurityIdentifier]::new($sid)),",
        "        $desired[$sid], 'ContainerInherit,ObjectInherit', 'None', 'Allow')",
        "    $acl.AddAccessRule($ace) | Out-Null",
        "}",
        "Set-Acl -LiteralPath $path -AclObject $acl",
        "Write-Host \"Droits synchronisés : $path ($($desired.Count) membre(s))\"",
    ]
    return "\n".join(lines) + "\n"


def save_script(content: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8-sig")
    return dest


# -- Persistance ---------------------------------------------------------------------

def load_class_space_config() -> ClassSpaceConfig:
    if CLASS_SPACE_CONFIG_FILE.exists():
        try:
            with CLASS_SPACE_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                return ClassSpaceConfig.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError):
            pass
    return ClassSpaceConfig()


def save_class_space_config(config: ClassSpaceConfig) -> None:
    CLASS_SPACE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CLASS_SPACE_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)


def load_class_space_state() -> dict[str, dict]:
    """{group_dn: {"path":…, "members":[…], "pending": bool}}"""
    if CLASS_SPACE_STATE_FILE.exists():
        try:
            with CLASS_SPACE_STATE_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError):
            pass
    return {}


def save_class_space_state(state: dict[str, dict]) -> None:
    CLASS_SPACE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CLASS_SPACE_STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)


def notify_group_created(group_dn: str, group_name: str = "") -> str | None:
    """Hook « création auto » appelé à la création d'un groupe de classe.

    Enregistre l'espace comme *en attente* (la création SMB effective a lieu
    sur le serveur de fichiers — script M18 déployé depuis la page du module)
    et crée le dossier si le chemin est accessible depuis cette machine.

    Retourne le chemin prévu, ou ``None`` si l'option est désactivée.
    """
    config = load_class_space_config()
    if not config.auto_on_group_create:
        return None
    name = group_name or (group_dn.split(",")[0].split("=", 1)[1] if "," in group_dn else group_dn)
    path = config.resolve_path(name)

    state = load_class_space_state()
    entry = state.setdefault(group_dn, {})
    entry.update({"path": path, "group_name": name, "pending": True})
    entry.setdefault("members", [])
    save_class_space_state(state)

    # Tentative de création du dossier uniquement sous Windows (UNC/local) :
    # sous Linux un chemin \\srv\... serait interprété comme un nom de fichier
    # relatif et créerait un dossier parasite.
    if os.name == "nt":
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            entry["folder_created"] = True
        except OSError:
            entry["folder_created"] = False
    else:
        entry["folder_created"] = False
    save_class_space_state(state)
    return path


def mark_synchronized(group_dn: str, members: Sequence[str]) -> None:
    """Enregistre l'état de synchronisation d'un espace."""
    state = load_class_space_state()
    entry = state.setdefault(group_dn, {})
    entry["members"] = sorted(members)
    entry["pending"] = False
    save_class_space_state(state)
