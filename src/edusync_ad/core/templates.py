"""M23 — Modèles de groupes (templates).

Un modèle décrit « à quoi ressemble un groupe scolaire » : OU parente +
motif de sous-OU, groupes auto-créés (motifs {classe}…), script de
connexion, quota FSRM, dossier personnel, espace partagé, profil,
heures de connexion, politique de mot de passe et licences Office 365.

- **Instanciation 1-clic** : saisie du nom de classe → création OU +
  groupes dans l'AD (idempotente), enregistrement des configs scopées
  profil/quota sur l'OU (celles que les M13/M14 résolvent
  groupe > OU > défaut), génération du paquet d'artefacts (script de
  connexion, script d'espaces, script FSRM, rapport JSON).
- **Duplication / export / import** JSON **et** XML (aller-retour
  strictement équivalent).

Champs directement appliqués à l'instanciation : OU, groupes, script
(artefact), quota + profil (configs scopées OU), espace (script).
Champs consignés dans le rapport pour être utilisés à la création des
comptes : politique MDP (génération), heures de connexion (onglet M16),
licences O365 (M19 — ``usage_location`` requis), dossier personnel
global (option ``apply_home_globally``).
"""

from __future__ import annotations

import base64
import json
import re
import string
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from ldap3.utils.dn import escape_rdn

from edusync_ad.core.class_spaces import (
    ClassSpaceConfig,
    ClassSpacePlan,
    creation_script,
    save_script,
)
from edusync_ad.core.config import config_dir
from edusync_ad.core.homedirs import HomeDirConfig, save_home_dir_config
from edusync_ad.core.logon_hours import LogonHoursPreset, grid_summary
from edusync_ad.core.logon_scripts import LogonScript, deploy_scripts
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.profiles import (
    ProfileConfig,
    ProfileType,
    dict_to_profile_config,
    load_all_profile_configs,
    profile_config_to_dict,
    save_all_profile_configs,
)
from edusync_ad.core.quotas import (
    QuotaManager,
    QuotaPlan,
    QuotaSettings,
    load_all_quota_configs,
    save_all_quota_configs,
)

TEMPLATES_FILE = config_dir() / "group_templates.json"

TEMPLATE_KINDS: dict[str, str] = {
    "classe_eleve": "Classe élève",
    "classe_prof": "Classe prof",
    "personnel_admin": "Personnel admin",
    "service_technique": "Service technique",
    "custom": "Personnalisé",
}

GROUP_SCOPES: dict[str, str] = {
    "global": "Globale",
    "domainlocal": "Locale au domaine",
    "universal": "Universelle",
}

# groupType AD = 0x80000000 (sécurité) | portée
GROUP_TYPE_VALUES: dict[str, int] = {
    "global": -2147483646,        # 0x80000002
    "domainlocal": -2147483644,   # 0x80000004
    "universal": -2147483640,     # 0x80000008
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# -- Motifs {champ} ---------------------------------------------------------------------

def _validate_pattern(pattern: str, label: str) -> list[str]:
    """Contrôle syntaxique d'un motif ``{champ}`` (accolades, nom de champ)."""
    errors: list[str] = []
    try:
        for _, field_name, _, _ in string.Formatter().parse(pattern or ""):
            if field_name is None:
                continue
            if not _FIELD_RE.fullmatch(field_name):
                errors.append(f"{label} : champ « {{{field_name}}} » invalide.")
    except ValueError as exc:
        errors.append(f"{label} : accolades non équilibrées ({exc}).")
    return errors


def _pattern_fields(pattern: str) -> list[str]:
    found: list[str] = []
    try:
        for _, field_name, _, _ in string.Formatter().parse(pattern or ""):
            if field_name and field_name not in found:
                found.append(field_name)
    except ValueError:
        pass  # signalé par validate()
    return found


def render_pattern(pattern: str, values: dict[str, str]) -> str:
    """Rend un motif avec les valeurs fournies (KeyError → ValueError lisible)."""
    try:
        return str(pattern).format_map({k: str(v) for k, v in values.items()})
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Impossible de rendre « {pattern} » : {exc}") from exc


def group_sam(name: str) -> str:
    """sAMAccountName d'un groupe à partir de son CN (caractères interdits → «-»)."""
    sam = re.sub(r'[\\/\[\]:;|=,+*?"<>]', "-", (name or "")).strip().rstrip(".")
    return sam or "groupe"


# -- Groupes ----------------------------------------------------------------------------

@dataclass
class GroupSpec:
    """Groupe auto-créé par le modèle."""

    name_pattern: str = ""
    scope: str = "global"
    description: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name_pattern.strip():
            errors.append("Nom de groupe vide.")
        if self.scope not in GROUP_SCOPES:
            errors.append(
                f"Portée inconnue « {self.scope} » "
                f"(attendu : {', '.join(GROUP_SCOPES)})."
            )
        errors.extend(_validate_pattern(self.name_pattern, "Groupe"))
        return errors

    def to_dict(self) -> dict:
        return {
            "name_pattern": self.name_pattern,
            "scope": self.scope,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroupSpec":
        return cls(
            name_pattern=str(data.get("name_pattern", "")),
            scope=str(data.get("scope", "global")),
            description=str(data.get("description", "")),
        )


# -- Modèle -----------------------------------------------------------------------------

@dataclass
class GroupTemplate:
    """Modèle de groupe : structure AD + politiques associées."""

    id: str
    name: str
    kind: str = "custom"
    description: str = ""
    ou_parent_dn: str = ""            # OU parente (choisie dans l'UI / à l'instanciation)
    ou_rdn_pattern: str = ""          # "" = aucun OU dédié (les groupes vont dans l'OU parente)
    groups: list[GroupSpec] = field(default_factory=list)
    logon_script: LogonScript | None = None
    quota: QuotaSettings | None = None
    home: HomeDirConfig | None = None
    space: ClassSpaceConfig | None = None
    profile: ProfileConfig | None = None
    hours_preset: str = ""            # nom LogonHoursPreset.builtin() ou ""
    password_policy: PasswordPolicy | None = None
    licenses: list[str] = field(default_factory=list)   # SKU Office 365

    # -- Validation ---------------------------------------------------------------

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not _ID_RE.fullmatch(self.id or ""):
            errors.append(
                f"Identifiant invalide « {self.id} » "
                "(minuscules, chiffres, « _ » et « - » uniquement)."
            )
        if not self.name.strip():
            errors.append("Le nom du modèle est vide.")
        if self.kind not in TEMPLATE_KINDS:
            errors.append(f"Type de modèle inconnu « {self.kind} ».")
        if self.ou_rdn_pattern:
            errors.extend(_validate_pattern(self.ou_rdn_pattern, "Motif d'OU"))
        if not self.groups:
            errors.append("Aucun groupe défini.")
        seen: set[str] = set()
        for index, spec in enumerate(self.groups, start=1):
            errors.extend(f"Groupe {index} : {err}" for err in spec.validate())
            key = spec.name_pattern.strip()
            if key and key in seen:
                errors.append(f"Groupe {index} : motif dupliqué « {key} ».")
            seen.add(key)
        if self.logon_script:
            errors.extend(self.logon_script.validate())
        if self.quota:
            errors.extend(self.quota.validate())
        if self.home:
            errors.extend(self.home.validate())
        if self.space:
            errors.extend(self.space.validate())
        if self.hours_preset and self.hours_preset not in _preset_names():
            errors.append(f"Préréglage d'heures inconnu « {self.hours_preset} ».")
        if self.password_policy and not (6 <= self.password_policy.longueur <= 128):
            errors.append("Longueur de mot de passe hors bornes (6 à 128).")
        for sku in self.licenses:
            if not str(sku).strip():
                errors.append("Licence O365 vide.")
        return errors

    # -- Sérialisation -------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "ou_parent_dn": self.ou_parent_dn,
            "ou_rdn_pattern": self.ou_rdn_pattern,
            "groups": [spec.to_dict() for spec in self.groups],
            "logon_script": self.logon_script.to_dict() if self.logon_script else None,
            "quota": self.quota.to_dict() if self.quota else None,
            "home": self.home.to_dict() if self.home else None,
            "space": self.space.to_dict() if self.space else None,
            "profile": profile_config_to_dict(self.profile) if self.profile else None,
            "hours_preset": self.hours_preset,
            "password_policy": asdict(self.password_policy) if self.password_policy else None,
            "licenses": list(self.licenses),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroupTemplate":
        policy = data.get("password_policy")
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "custom")),
            description=str(data.get("description", "")),
            ou_parent_dn=str(data.get("ou_parent_dn", "")),
            ou_rdn_pattern=str(data.get("ou_rdn_pattern", "")),
            groups=[GroupSpec.from_dict(g) for g in data.get("groups", []) or []],
            logon_script=(
                LogonScript.from_dict(data["logon_script"])
                if data.get("logon_script") else None
            ),
            quota=(
                QuotaSettings.from_dict(data["quota"]) if data.get("quota") else None
            ),
            home=(
                HomeDirConfig.from_dict(data["home"]) if data.get("home") else None
            ),
            space=(
                ClassSpaceConfig.from_dict(data["space"]) if data.get("space") else None
            ),
            profile=(
                dict_to_profile_config(data["profile"]) if data.get("profile") else None
            ),
            hours_preset=str(data.get("hours_preset", "")),
            password_policy=(
                _policy_from_dict(policy) if isinstance(policy, dict) else None
            ),
            licenses=[str(sku) for sku in data.get("licenses", []) or []],
        )

    def clone(self) -> "GroupTemplate":
        """Copie profonde (duplication de modèle)."""
        return GroupTemplate.from_dict(self.to_dict())

    # -- Valeurs d'instanciation ----------------------------------------------------

    def placeholders(self) -> list[str]:
        """Champs ``{…}`` utilisés par les motifs (OU + groupes)."""
        found: list[str] = []
        for pattern in [self.ou_rdn_pattern] + [g.name_pattern for g in self.groups]:
            for name in _pattern_fields(pattern):
                if name not in found:
                    found.append(name)
        return found


def _policy_from_dict(data: dict) -> PasswordPolicy:
    known = set(PasswordPolicy.__dataclass_fields__)
    return PasswordPolicy(**{k: v for k, v in data.items() if k in known})


def _preset_names() -> list[str]:
    return [preset.name for preset in LogonHoursPreset.builtin()]


def validate_values(template: GroupTemplate, values: dict[str, str]) -> list[str]:
    """Valeurs manquantes pour rendre les motifs du modèle."""
    errors: list[str] = []
    for name in template.placeholders():
        if not str(values.get(name, "")).strip():
            errors.append(f"Valeur manquante pour « {name} ».")
    return errors


# -- Instanciation ----------------------------------------------------------------------

@dataclass
class GroupAction:
    name: str
    dn: str
    sam: str
    scope: str
    created: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InstantiationResult:
    template_id: str
    template_name: str
    values: dict[str, str] = field(default_factory=dict)
    ou_dn: str = ""
    ou_created: bool = False
    groups: list[GroupAction] = field(default_factory=list)
    artifacts: list[Path] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    report_path: str = ""

    @property
    def created_groups(self) -> list[GroupAction]:
        return [g for g in self.groups if g.created]

    def summary(self) -> str:
        lines = [f"Modèle « {self.template_name} » instancié."]
        if self.ou_dn:
            state = "créée" if self.ou_created else "existante"
            lines.append(f"OU : {self.ou_dn} ({state}).")
        created = len(self.created_groups)
        existing = len(self.groups) - created
        detail = f"{len(self.groups)} groupe(s)"
        if created:
            detail += f", {created} créé(s)"
        if existing:
            detail += f", {existing} déjà présent(s)"
        lines.append(detail + ".")
        if self.artifacts:
            lines.append(f"{len(self.artifacts)} artefact(s) généré(s).")
        lines.extend(f"✓ {item}" for item in self.registered)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "modele": self.template_name,
            "id": self.template_id,
            "valeurs": dict(self.values),
            "ou_dn": self.ou_dn,
            "ou_creee": self.ou_created,
            "groupes": [g.to_dict() for g in self.groups],
            "artefacts": [str(p) for p in self.artifacts],
            "enregistres": list(self.registered),
            "notes": list(self.notes),
            "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


def instantiate(
    connection,
    template: GroupTemplate,
    values: dict[str, str],
    *,
    ou_parent_dn: str | None = None,
    artifacts_dir: Path | None = None,
    register_scoped: bool = True,
    apply_home_globally: bool = False,
    domain: str = "",
) -> InstantiationResult:
    """Instancie un modèle en 1-clic : OU + groupes dans l'AD, configs
    scopées (profil / quota) sur l'OU, artefacts (scripts + rapport).

    Idempotente : une OU ou un groupe déjà présent n'est pas recréé.
    ``ou_parent_dn`` permet de choisir la cible à la volée (sinon celle du
    modèle). Les échecs AD lèvent ``ADError``, la validation ``ValueError``.
    """
    errors = template.validate() + validate_values(template, values)
    if errors:
        raise ValueError("; ".join(errors))
    parent = (ou_parent_dn if ou_parent_dn is not None else template.ou_parent_dn).strip()
    if not parent:
        raise ValueError("Aucune OU parente (à renseigner dans le modèle ou à l'instanciation).")

    result = InstantiationResult(
        template_id=template.id,
        template_name=template.name,
        values={k: str(v) for k, v in values.items()},
    )

    # 0. Cible calculée + script re-scopé validé AVANT toute écriture AD
    container = parent
    ou_dn = ""
    rdn = ""
    if template.ou_rdn_pattern:
        rdn = render_pattern(template.ou_rdn_pattern, values)
        ou_dn = f"ou={escape_rdn(rdn)},{parent}"
        container = ou_dn
    script = None
    if template.logon_script:
        script = template.logon_script
        if ou_dn:
            script = replace(script, scope_type="ou", scope_dn=ou_dn)
        script_errors = script.validate()
        if script_errors:
            raise ValueError("Script de connexion : " + "; ".join(script_errors))

    # 1. OU dédiée (si motif) — idempotente
    if ou_dn:
        children = {child.lower() for child in connection.list_ou_children(parent)}
        exists = (
            f"ou={rdn}".lower() in children
            or f"ou={escape_rdn(rdn)}".lower() in children
        )
        if not exists:
            connection.create_ou(ou_dn, rdn)
        result.ou_dn = ou_dn
        result.ou_created = not exists
        container = ou_dn

    # 2. Groupes auto — idempotents
    existing = {dn.lower() for dn, _ in connection.list_groups(container)}
    for spec in template.groups:
        name = render_pattern(spec.name_pattern, values)
        dn = f"cn={escape_rdn(name)},{container}"
        sam = group_sam(name)
        created = (
            dn.lower() not in existing
            and f"cn={name},{container}".lower() not in existing
        )
        if created:
            connection.create_group(dn, sam, group_type=GROUP_TYPE_VALUES[spec.scope])
            existing.add(dn.lower())
        result.groups.append(
            GroupAction(name=name, dn=dn, sam=sam, scope=spec.scope, created=created)
        )

    # 3. Configs scopées OU (M13 profil, M14 quota — résolution groupe > OU > défaut)
    if register_scoped and result.ou_dn:
        if template.profile:
            ou_cfg, grp_cfg, default_cfg = load_all_profile_configs()
            ou_cfg[result.ou_dn] = template.profile
            save_all_profile_configs(ou_cfg, grp_cfg, default_cfg)
            result.registered.append(f"profil (profilPath) → {result.ou_dn}")
        if template.quota:
            ou_q, grp_q, default_q = load_all_quota_configs()
            ou_q[result.ou_dn] = template.quota
            save_all_quota_configs(ou_q, grp_q, default_q)
            result.registered.append(
                f"quota dossier personnel ({template.quota.size_gb:g} Go) → {result.ou_dn}"
            )
    if template.home and apply_home_globally:
        save_home_dir_config(template.home)
        result.registered.append(
            "configuration globale des dossiers personnels (remplacée)"
        )

    # 4. Artefacts
    if artifacts_dir is not None:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        if script is not None:
            result.artifacts.extend(deploy_scripts([script], artifacts_dir))

        space_plans: list[ClassSpacePlan] = []
        if template.space:
            space_plans = [
                ClassSpacePlan(
                    group_dn=action.dn,
                    group_name=action.name,
                    path=template.space.resolve_path(action.name),
                )
                for action in result.groups
            ]
            content = creation_script(
                space_plans, domain, teachers_group=template.space.teachers_group
            )
            result.artifacts.append(save_script(content, artifacts_dir / "espaces_classes.ps1"))

        if template.quota and space_plans:
            plans = [
                QuotaPlan(user_dn="", sam="", home_path=plan.path, settings=template.quota)
                for plan in space_plans
            ]
            result.artifacts.append(
                QuotaManager().save_powershell_script(
                    plans, artifacts_dir / "quotas_fsr.ps1"
                )
            )

        report = result.to_dict()
        report["heures_connexion"] = _hours_report(template)
        report["politique_mdp"] = (
            asdict(template.password_policy) if template.password_policy else None
        )
        report["licences_o365"] = list(template.licenses)
        report["dossier_personnel"] = template.home.to_dict() if template.home else None
        report["ou_parente"] = parent
        filename = f"instantiation-{_safe_name(template.id, values)}.json"
        report_path = artifacts_dir / filename
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        result.artifacts.append(report_path)
        result.report_path = str(report_path)

    # 5. Notes d'application différée (champs consignés, non appliqués ici)
    if template.password_policy:
        result.notes.append(
            "Politique de mot de passe : appliquée à la génération lors de la "
            "création des comptes (réglage MDP de la création de comptes)."
        )
    if template.hours_preset:
        result.notes.append(
            f"Heures de connexion « {template.hours_preset} » : à appliquer aux "
            "comptes depuis l'onglet Heures de connexion (M16)."
        )
    if template.licenses:
        result.notes.append(
            "Licences O365 : à assigner via le module Microsoft 365 (M19) — "
            "usage_location requis avant assignation."
        )
    if template.home and not apply_home_globally:
        result.notes.append(
            "Dossier personnel : appliqué à la création des comptes (M17) — "
            "cocher « config globale » pour remplacer la configuration générale."
        )
    return result


def _hours_report(template: GroupTemplate) -> dict | None:
    if not template.hours_preset:
        return None
    for preset in LogonHoursPreset.builtin():
        if preset.name == template.hours_preset:
            return {"preset": preset.name, "resume": grid_summary(preset.build())}
    return None


def _safe_name(template_id: str, values: dict[str, str]) -> str:
    first = next((str(v) for v in values.values() if str(v).strip()), template_id)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", first).strip("-.")
    return cleaned or template_id


# -- Persistance ------------------------------------------------------------------------

def _roaming_profile() -> ProfileConfig:
    return ProfileConfig(
        profile_type=ProfileType.ROAMING,
        roaming_path=r"\\srv\profils\%USERNAME%",
    )


def _accueil_script() -> LogonScript:
    return LogonScript(
        name="message-accueil",
        kind="bat",
        timing="logon",
        scope_type="default",
        content=(
            "@echo off\r\n"
            "echo ============================================\r\n"
            "echo  Bienvenue %FULLNAME% (%USERNAME%)\r\n"
            "echo  Classe / OU : %OU%\r\n"
            "echo  Mail : %EMAIL%\r\n"
            "echo ============================================\r\n"
        ),
    )


def _mapping_script() -> LogonScript:
    return LogonScript(
        name="mapping-lecteur-h",
        kind="bat",
        timing="logon",
        scope_type="default",
        content=(
            "@echo off\r\n"
            'if not "%HOMEDIR%"=="" (\r\n'
            "    net use H: \"%HOMEDIR%\" /persistent:yes\r\n"
            ")\r\n"
        ),
    )


def builtin_templates() -> list[GroupTemplate]:
    """Les 4 modèles fournis par défaut (chemins serveur à adapter)."""
    return [
        GroupTemplate(
            id="classe_eleve",
            name="Classe élève",
            kind="classe_eleve",
            description=(
                "OU de classe, groupe des élèves, espace partagé en écriture, "
                "quota 5 Go, profil itinérant, lecteur H:, heures de cours."
            ),
            ou_rdn_pattern="Eleves-{classe}",
            groups=[
                GroupSpec("{classe}", "global", "Groupe de classe (élèves + espace)"),
            ],
            logon_script=_mapping_script(),
            quota=QuotaSettings(size_gb=5, warning_percent=80, hard_limit=False),
            home=HomeDirConfig(),
            space=ClassSpaceConfig(),
            profile=_roaming_profile(),
            hours_preset="Heures cours",
            password_policy=PasswordPolicy(
                longueur=12, majuscules=True, chiffres=True, caracteres_speciaux=True
            ),
            licenses=[],
        ),
        GroupTemplate(
            id="classe_prof",
            name="Classe prof",
            kind="classe_prof",
            description=(
                "Groupe des enseignants affectés à une classe, larges heures "
                "de connexion, profil itinérant, message d'accueil."
            ),
            ou_rdn_pattern="Profs-{classe}",
            groups=[
                GroupSpec("{classe}-Profs", "global", "Enseignants de la classe"),
            ],
            logon_script=_accueil_script(),
            home=HomeDirConfig(),
            profile=_roaming_profile(),
            hours_preset="Heures admin",
            password_policy=PasswordPolicy(
                longueur=14, majuscules=True, chiffres=True, caracteres_speciaux=True
            ),
        ),
        GroupTemplate(
            id="personnel_admin",
            name="Personnel admin",
            kind="personnel_admin",
            description=(
                "Personnel administratif et direction : OU dédiée, deux groupes, "
                "politique de mot de passe renforcée, heures administrateur."
            ),
            ou_rdn_pattern="Personnel-Admin",
            groups=[
                GroupSpec("Personnel-Admin", "global", "Personnel administratif"),
                GroupSpec("Administrateurs", "universal", "Administrateurs de l'établissement"),
            ],
            home=HomeDirConfig(),
            profile=_roaming_profile(),
            hours_preset="Heures admin",
            password_policy=PasswordPolicy(
                longueur=16, majuscules=True, chiffres=True, caracteres_speciaux=True
            ),
        ),
        GroupTemplate(
            id="service_technique",
            name="Service technique",
            kind="service_technique",
            description=(
                "Service technique / informatique : aucun filtrage horaire "
                "(exigence de permanence), mot de passe renforcé, espace de service."
            ),
            ou_rdn_pattern="Services-Techniques",
            groups=[
                GroupSpec("Service-Technique", "global", "Agents du service technique"),
            ],
            space=ClassSpaceConfig(
                share_root=r"\\srv\services", folder_template="%GROUP%"
            ),
            home=HomeDirConfig(),
            profile=_roaming_profile(),
            password_policy=PasswordPolicy(
                longueur=16, majuscules=True, chiffres=True, caracteres_speciaux=True
            ),
        ),
    ]


def load_templates() -> list[GroupTemplate]:
    """Charge les modèles du fichier ; fichier absent/corrompu → modèles intégrés."""
    if TEMPLATES_FILE.exists():
        try:
            with TEMPLATES_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            items = data.get("modeles", data) if isinstance(data, dict) else data
            templates = [GroupTemplate.from_dict(item) for item in items]
            return [t for t in templates if not _all_invalid(t)]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return builtin_templates()


def _all_invalid(template: GroupTemplate) -> bool:
    """Fichier corrompu : l'élément n'a ni id ni nom exploitable."""
    return not template.id.strip() and not template.name.strip()


def save_templates(templates: list[GroupTemplate], path: Path | None = None) -> Path:
    dest = path if path is not None else TEMPLATES_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(
            {"modeles": [t.to_dict() for t in templates]},
            fh, indent=2, ensure_ascii=False,
        )
    return dest


def unique_template_id(templates: list[GroupTemplate], base: str) -> str:
    """Identifiant libre pour une duplication (base, base-2, base-3…)."""
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", (base or "").strip().lower())
        if not unicodedata.combining(char)
    )
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", folded).strip("-") or "modele"
    if not _ID_RE.fullmatch(cleaned):
        cleaned = "modele"
    candidate = cleaned
    existing = {t.id for t in templates}
    counter = 2
    while candidate in existing:
        candidate = f"{cleaned}-{counter}"
        counter += 1
    return candidate


def duplicate_template(
    templates: list[GroupTemplate], template: GroupTemplate, new_name: str
) -> GroupTemplate:
    """Copie profonde avec nouvel id/nom — la liste n'est pas modifiée."""
    clone = template.clone()
    clone.id = unique_template_id(templates, template.id)
    clone.name = new_name.strip() or f"{template.name} (copie)"
    return clone


# -- Export / import JSON ----------------------------------------------------------------

def export_template(template: GroupTemplate, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(template.to_dict(), fh, indent=2, ensure_ascii=False)
    return dest


def import_template(path: Path) -> GroupTemplate:
    """Import JSON d'un modèle (échoue si invalide)."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Lecture du modèle impossible : {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Format de modèle invalide (objet JSON attendu).")
    template = GroupTemplate.from_dict(data)
    errors = template.validate()
    if errors:
        raise ValueError("Modèle invalide : " + "; ".join(errors))
    return template


# -- Export / import XML (aller-retour strictement équivalent au JSON) --------------------

def _value_to_xml(parent: ET.Element, tag: str, value) -> ET.Element:
    if value is None:
        child = ET.SubElement(parent, tag, type="null")
        return child
    if isinstance(value, bool):
        child = ET.SubElement(parent, tag, type="bool")
        child.text = "true" if value else "false"
        return child
    if isinstance(value, (int, float)):
        child = ET.SubElement(parent, tag, type="number")
        child.text = repr(value)
        return child
    if isinstance(value, dict):
        child = ET.SubElement(parent, tag, type="dict")
        for key, item in value.items():
            _value_to_xml(child, str(key), item)
        return child
    if isinstance(value, (list, tuple)):
        child = ET.SubElement(parent, tag, type="list")
        for item in value:
            _value_to_xml(child, "item", item)
        return child
    text = str(value)
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        # \r et caractères de contrôle : le parseur XML les normalise / refuse
        # les écrit bruts → base64 pour un aller-retour strictement équivalent
        child = ET.SubElement(parent, tag, type="str", encoding="b64")
        child.text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return child
    child = ET.SubElement(parent, tag, type="str")
    child.text = text
    return child


def _xml_to_value(node: ET.Element):
    kind = node.get("type", "str")
    text = node.text or ""
    if kind == "null":
        return None
    if kind == "bool":
        return text.strip().lower() == "true"
    if kind == "number":
        number = float(text)
        return int(number) if number.is_integer() else number
    if kind == "dict":
        return {child.tag: _xml_to_value(child) for child in node}
    if kind == "list":
        return [_xml_to_value(child) for child in node]
    if node.get("encoding") == "b64":
        return base64.b64decode(text.encode("ascii")).decode("utf-8")
    return text


def export_template_xml(template: GroupTemplate, dest: Path) -> Path:
    root = ET.Element("modele", type="dict")
    for key, value in template.to_dict().items():
        _value_to_xml(root, str(key), value)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(dest, encoding="utf-8", xml_declaration=True)
    return dest


def import_template_xml(path: Path) -> GroupTemplate:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Lecture du modèle XML impossible : {exc}") from exc
    root = tree.getroot()
    if root.tag != "modele":
        raise ValueError(f"Racine XML inattendue « {root.tag} » (attendu : modèle).")
    data = _xml_to_value(root)
    if not isinstance(data, dict):
        raise ValueError("Format de modèle XML invalide.")
    template = GroupTemplate.from_dict(data)
    errors = template.validate()
    if errors:
        raise ValueError("Modèle invalide : " + "; ".join(errors))
    return template
