"""Scripts logon / logoff (M15).

Éditeur de scripts (``bat`` / ``ps1`` / ``vbs``) par OU ou groupe, avec
variables EduSync : ``%USERNAME%``, ``%FULLNAME%``, ``%OU%``, ``%GROUP%``,
``%EMAIL%``, ``%HOMEDIR%``.

Déploiement :
- **Logon** — écriture du script sur le partage NETLOGON + attribut AD
  ``scriptPath`` (relatif au partage NETLOGON du domaine).
- **Logoff** — les scripts de déconnexion ne sont pas définissables par compte
  en LDAP : le fichier est déployé et la liaison GPO (Computer Configuration →
  Windows Settings → Scripts) est à réaliser dans la GPMC — le module génère la
  note de déploiement correspondante.

Variable inconnue dans le contenu : laissée intacte (les variables natives
Windows comme ``%USERDOMAIN%`` passent alors au shell).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from edusync_ad.core.config import config_dir

SCRIPT_CONFIG_FILE = config_dir() / "logon_scripts.json"

SUPPORTED_KINDS = ("bat", "ps1", "vbs")
SUPPORTED_TIMINGS = ("logon", "logoff")

# Variable → libellé affiché dans l'éditeur
SCRIPT_VARIABLES: dict[str, str] = {
    "%USERNAME%": "Identifiant (sAMAccountName)",
    "%FULLNAME%": "Nom complet",
    "%OU%": "OU la plus proche",
    "%GROUP%": "Premier groupe",
    "%EMAIL%": "Adresse e-mail",
    "%HOMEDIR%": "Dossier personnel",
}

_VAR_RE = re.compile(r"%[A-Z_]+%")


def deepest_ou_name(dn: str) -> str:
    """Nom de l'OU la plus proche d'un DN (OU=… le plus à gauche)."""
    if not dn:
        return ""
    for part in dn.split(","):
        if part.strip().upper().startswith("OU="):
            return part.split("=", 1)[1]
    return ""


def group_cn(group_dn: str) -> str:
    """CN d'un DN de groupe (premier composant CN=, sans le reste du DN)."""
    if group_dn.upper().startswith("CN="):
        return group_dn.split("=", 1)[1].split(",")[0]
    return group_dn


def build_context(
    user: dict,
    ou_dn: str = "",
    groups: Sequence[str] = (),
    domain: str = "",
) -> dict[str, str]:
    """Contexte de rendu pour un compte : variables EduSync → valeurs."""
    user_dn = user.get("dn", "")
    return {
        "%USERNAME%": user.get("sam", ""),
        "%FULLNAME%": user.get("cn") or user.get("display_name", ""),
        "%OU%": deepest_ou_name(ou_dn or user_dn),
        "%GROUP%": group_cn(groups[0]) if groups else "",
        "%EMAIL%": user.get("mail", ""),
        "%HOMEDIR%": user.get("homeDirectory", ""),
        "%DOMAIN%": domain,
    }


def render_script(content: str, context: dict[str, str]) -> str:
    """Remplace les variables connues ; les autres %VAR% restent intactes."""
    for var, value in context.items():
        content = content.replace(var, value)
    return content


def unknown_variables(content: str) -> list[str]:
    """Variables %X% du contenu qui ne font pas partie du contexte EduSync."""
    return sorted({m for m in _VAR_RE.findall(content) if m not in SCRIPT_VARIABLES})


@dataclass
class LogonScript:
    """Script enregistré + portée d'application."""

    name: str                       # nom logique (sans extension imposée)
    kind: str = "bat"               # bat | ps1 | vbs
    timing: str = "logon"           # logon | logoff
    scope_type: str = "default"     # default | ou | group
    scope_dn: str = ""
    content: str = ""

    @property
    def filename(self) -> str:
        """Nom de fichier déployé (extension normalisée)."""
        base = re.sub(r"[^\w\-. ]", "_", self.name).strip() or "script"
        ext = self.kind if base.lower().endswith(f".{self.kind}") else f".{self.kind}"
        return base if base.lower().endswith(f".{self.kind}") else f"{base}{ext}"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("Le nom du script est vide.")
        if any(sep in self.name for sep in ("/", "\\")):
            errors.append("Le nom du script ne doit pas contenir de séparateur de chemin.")
        if self.kind not in SUPPORTED_KINDS:
            errors.append(f"Type de script non supporté : {self.kind} "
                          f"(attendu : {', '.join(SUPPORTED_KINDS)}).")
        if self.timing not in SUPPORTED_TIMINGS:
            errors.append(
                f"Moment non supporté : {self.timing} "
                f"(attendu : {', '.join(SUPPORTED_TIMINGS)})."
            )
        if self.scope_type not in ("default", "ou", "group"):
            errors.append(f"Portée non supportée : {self.scope_type}.")
        if self.scope_type in ("ou", "group") and not self.scope_dn:
            errors.append("La portée OU/groupe ne désigne aucun objet.")
        if not self.content.strip():
            errors.append("Le contenu du script est vide.")
        return errors

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "timing": self.timing,
            "scope_type": self.scope_type,
            "scope_dn": self.scope_dn,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LogonScript":
        return cls(
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "bat")),
            timing=str(data.get("timing", "logon")),
            scope_type=str(data.get("scope_type", "default")),
            scope_dn=str(data.get("scope_dn", "")),
            content=str(data.get("content", "")),
        )


@dataclass
class LogonScriptTemplate:
    """Modèle d'écran d'accueil / mapping intégré."""

    name: str
    description: str
    kind: str
    content: str

    @classmethod
    def builtin(cls) -> list["LogonScriptTemplate"]:
        return [
            cls(
                name="Message d'accueil",
                description="Bannière simple en .bat avec identité de l'utilisateur",
                kind="bat",
                content=(
                    "@echo off\r\n"
                    "echo ============================================\r\n"
                    "echo  Bienvenue %FULLNAME% (%USERNAME%)\r\n"
                    "echo  Classe / OU : %OU%\r\n"
                    "echo  Groupe : %GROUP%\r\n"
                    "echo  Mail : %EMAIL%\r\n"
                    "echo ============================================\r\n"
                ),
            ),
            cls(
                name="Mapping du lecteur H:",
                description="Monte le dossier personnel sur H: (bat)",
                kind="bat",
                content=(
                    "@echo off\r\n"
                    "if not \"%HOMEDIR%\"==\"\" (\r\n"
                    "    net use H: \"%HOMEDIR%\" /persistent:yes\r\n"
                    ")\r\n"
                ),
            ),
            cls(
                name="Journal de session (PowerShell)",
                description="Trace la connexion dans un fichier .log (ps1)",
                kind="ps1",
                content=(
                    "$log = Join-Path $env:TEMP \"edusync_logon_%USERNAME%.log\"\r\n"
                    "\"$(Get-Date -Format s) - %FULLNAME% (%OU%)\" | Out-File -Append $log\r\n"
                ),
            ),
        ]


# -- Déploiement ---------------------------------------------------------------------

def script_file_encoding(kind: str) -> tuple[str, bool]:
    """(encodage, ajouter un BOM) pour un type de script.

    ``cmd.exe`` échoue sur un fichier .bat commençant par un BOM : jamais de BOM
    pour bat/vbs. PowerShell lit l'UTF-8 avec BOM sans ambiguïté (accents).
    """
    if kind == "ps1":
        return "utf-8-sig", True
    return "utf-8", False


def deploy_scripts(scripts: Sequence[LogonScript], dest_dir: Path) -> list[Path]:
    """Écrit les scripts dans un répertoire (NETLOGON, lecteur local…)."""
    written: list[Path] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    for script in scripts:
        encoding, _ = script_file_encoding(script.kind)
        path = dest_dir / script.filename
        path.write_text(script.content, encoding=encoding, newline="")
        written.append(path)
    return written


def netlogon_path(domain: str) -> str:
    """Chemin UNC du partage NETLOGON du domaine."""
    root = domain.split(".")[0] if domain else ""
    return f"\\\\{root}\\NETLOGON" if root else ""


def gpo_logoff_note(script: LogonScript) -> str:
    """Instructions de déploiement pour un script logoff (liaison GPO)."""
    fallback = "\\\\domaine\\NETLOGON"
    share = netlogon_path("domaine") or fallback
    return (
        "Script de déconnexion (logoff) — déploiement GPO requis :\n"
        f"1. Copiez « {script.filename} » dans {share}\n"
        "2. GPMC → GPO de la OU → Configuration ordinateur → Stratégies → "
        "Paramètres Windows → Scripts (démarrage/arrêt) → Déconnexion\n"
        "3. Ajoutez le script puis liez la GPO à la OU cible.\n"
        "L'attribut AD scriptPath ne concerne que la connexion (logon)."
    )


@dataclass
class ScriptPlan:
    """Script applicable à un compte précis."""

    user_dn: str
    sam: str
    script: LogonScript | None

    @property
    def script_path_value(self) -> str:
        """Valeur de l'attribut scriptPath ("" = supprimer)."""
        return self.script.filename if self.script else ""


class LogonScriptManager:
    """Résolution des scripts par compte (priorité groupe > OU > défaut)."""

    def __init__(self, scripts: Sequence[LogonScript] = ()) -> None:
        self.scripts: list[LogonScript] = list(scripts)

    @property
    def logon_scripts(self) -> list[LogonScript]:
        return [s for s in self.scripts if s.timing == "logon"]

    def resolve_for_user(
        self, ou_dn: str = "", groups: Sequence[str] | None = None
    ) -> LogonScript | None:
        candidates = self.logon_scripts
        if groups:
            for group_dn in groups:
                for s in candidates:
                    if s.scope_type == "group" and s.scope_dn == group_dn:
                        return s
        current = ou_dn
        while current:
            for s in candidates:
                if s.scope_type == "ou" and s.scope_dn == current:
                    return s
            parts = current.split(",", 1)
            current = parts[1] if len(parts) > 1 else ""
        for s in candidates:
            if s.scope_type == "default":
                return s
        return None

    def plans_for_users(
        self,
        users: Sequence[dict],
        ou_dn: str = "",
        groups_by_sam: dict[str, list[str]] | None = None,
    ) -> list[ScriptPlan]:
        groups_by_sam = groups_by_sam or {}
        return [
            ScriptPlan(
                user_dn=u.get("dn", ""),
                sam=u.get("sam", ""),
                script=self.resolve_for_user(ou_dn, groups_by_sam.get(u.get("sam", ""))),
            )
            for u in users
        ]


# -- Persistance -------------------------------------------------------------------------

def load_logon_scripts() -> list[LogonScript]:
    if SCRIPT_CONFIG_FILE.exists():
        try:
            with SCRIPT_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return [LogonScript.from_dict(d) for d in data if isinstance(d, dict)]
        except (OSError, ValueError, TypeError):
            pass
    return []


def save_logon_scripts(scripts: Sequence[LogonScript]) -> None:
    SCRIPT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SCRIPT_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump([s.to_dict() for s in scripts], fh, indent=2, ensure_ascii=False)
