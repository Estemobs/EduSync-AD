"""Office 365 / Microsoft Entra ID — logique métier (M19).

- **Comptes cloud** : plan de création depuis l'AD + attribution des licences
  éducation (A1 / A3 / A5, packs Office) résolues via ``/subscribedSkus``.
- **Groupes AD → Entra** : groupes de distribution, de sécurité ou Microsoft 365,
  avec création automatique de l'équipe Teams rattachée.
- **Photos d'identité → cloud** : mapping des fichiers (conventions M12) puis
  dépôt de la photo de profil via Graph.
- **Hybride (PHS / ADFS / PTA)** : ces mécanismes se configurent côté
  *Microsoft Entra Connect*, jamais via Graph — le module fournit donc une
  check-list d'exploitation + un rapport exportable plutôt qu'une fausse
  promesse d'automatisation.

La création d'un compte dans Entra ID exige ``usageLocation`` avant toute
attribution de licence (voir :class:`~edusync_ad.core.graph.GraphConfig`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from edusync_ad.core.graph import (
    GraphClient,
    GraphConfig,
    GraphError,
    load_m365_config,
    save_m365_config,
)
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import generate_password
from edusync_ad.core.photos import PhotoError, map_photos_convention, process_photo_from_path

# Licences éducation connues (skuPartNumber retourné par /subscribedSkus)
EDUCATION_LICENSES: dict[str, str] = {
    "M365EDU_A1": "Microsoft 365 A1 (Éducation)",
    "M365EDU_A3_FACULTY": "Microsoft 365 A3 — enseignants",
    "M365EDU_A3_STUDENT": "Microsoft 365 A3 — élèves",
    "M365EDU_A5_FACULTY": "Microsoft 365 A5 — enseignants",
    "M365EDU_A5_STUDENT": "Microsoft 365 A5 — élèves",
    "ENTERPRISEPACK": "Microsoft 365 E3",
}
DEFAULT_LICENSE = "M365EDU_A1"

GROUP_KINDS: dict[str, str] = {
    "m365": "Groupe Microsoft 365 (base d'un Teams)",
    "distribution": "Groupe de distribution",
    "security": "Groupe de sécurité",
}


def cloud_password_policy() -> PasswordPolicy:
    """Politique imposée par Entra ID (majuscules + chiffres + spéciaux)."""
    return PasswordPolicy(
        longueur=20, majuscules=True, chiffres=True, caracteres_speciaux=True
    )


def mail_nickname(name: str) -> str:
    """Normalise un nom de groupe en ``mailNickname`` autorisé par Entra ID.

    Accents retirés, minuscules, seuls ``[a-z0-9._-]`` conservés (les espaces
    et caractères réservés ``#&\\`` sont supprimés).
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9._-]", "", text)
    return text.strip(".") or "groupe"


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


# -- Plans ------------------------------------------------------------------------------

@dataclass
class CloudUserPlan:
    """Compte cloud à créer (ou déjà présent)."""

    sam: str
    upn: str
    prenom: str
    nom: str
    display_name: str
    disabled: bool = False
    existing: bool = False
    password: str = ""

    @property
    def label(self) -> str:
        status = "existant" if self.existing else "à créer"
        return f"{self.sam} → {self.upn}  [{status}]"


@dataclass
class GroupPlan:
    """Groupe Entra à créer/synchroniser depuis un groupe AD."""

    name: str
    mail_nickname: str
    kind: str = "m365"
    members: list[str] = field(default_factory=list)   # UPN
    existing_id: str = ""

    @property
    def label(self) -> str:
        status = "existant" if self.existing_id else "à créer"
        return f"{self.name}  [{status}, {len(self.members)} membre(s)]"


@dataclass
class SyncResult:
    """Issue d'une opération de synchronisation (une ligne de lot)."""

    key: str
    action: str          # "created" | "exists" | "updated" | "error"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "error"


def build_user_plans(
    users: Sequence[dict],
    domain: str,
    existing_upns: Iterable[str] = (),
) -> list[CloudUserPlan]:
    """Prévoit les comptes cloud à partir de comptes AD (sam, cn, disabled)."""
    existing = {u.strip().lower() for u in existing_upns}
    plans: list[CloudUserPlan] = []
    for user in users:
        sam = str(user.get("sam") or "").strip()
        if not sam:
            continue
        cn = str(user.get("cn") or sam)
        prenom, nom = split_display_name(cn, sam)
        upn = f"{sam}@{domain}" if domain else sam
        plans.append(
            CloudUserPlan(
                sam=sam,
                upn=upn,
                prenom=prenom,
                nom=nom,
                display_name=cn,
                disabled=bool(user.get("disabled")),
                existing=upn.lower() in existing,
            )
        )
    return plans


def build_group_plans(
    group_specs: Sequence[dict],
    kind: str = "m365",
    existing_by_nickname: dict[str, str] | None = None,
) -> list[GroupPlan]:
    """``group_specs`` : ``{"name": …, "members": [upn, …]}``.

    ``existing_by_nickname`` : ``mailNickname`` (minuscules) → ``id`` du groupe
    Entra déjà présent — permet de ne créer que ce qui manque.
    """
    existing = {k.lower(): v for k, v in (existing_by_nickname or {}).items()}
    plans: list[GroupPlan] = []
    seen: set[str] = set()
    for spec in group_specs:
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        nick = mail_nickname(name)
        if nick in seen:
            continue
        seen.add(nick)
        plans.append(
            GroupPlan(
                name=name,
                mail_nickname=nick,
                kind=kind,
                members=[str(m) for m in spec.get("members", [])],
                existing_id=existing.get(nick, ""),
            )
        )
    return plans


def build_photo_mapping(
    photo_dir: Path, users: Sequence[dict], domain: str
) -> dict[str, Path]:
    """Conventions M12 (``identifiant.jpg``, ``prenom_nom.jpg``…) → UPN."""
    pseudo = []
    for user in users:
        sam = str(user.get("sam") or "")
        cn = str(user.get("cn") or sam)
        prenom, nom = split_display_name(cn, sam)
        pseudo.append({"identifiant": sam, "prenom": prenom, "nom": nom})
    mapping = map_photos_convention(Path(photo_dir), pseudo)
    return {
        (f"{sam}@{domain}" if domain else sam): path
        for sam, path in mapping.matched.items()
    }


# -- Manager ------------------------------------------------------------------------------

class M365Manager:
    """Enchaîne les opérations Graph du module M19.

    Le ``client`` est injectable : les tests pilotent un faux client, l'UI
    utilise un :class:`~edusync_ad.core.graph.GraphClient` réel.
    """

    def __init__(
        self,
        config: GraphConfig | None = None,
        client: GraphClient | None = None,
    ) -> None:
        self.config = config if config is not None else load_m365_config()
        self.client = client if client is not None else GraphClient(self.config)

    # -- Connexion ------------------------------------------------------------------------

    def test_connection(self) -> str:
        """Valide la connexion + les permissions. Retourne le nom du locataire."""
        organization = self.client.organization()
        return str(organization.get("displayName") or self.config.tenant_id or "OK")

    def available_licenses(self) -> dict[str, dict[str, str]]:
        """Licences éducation réellement souscrites : partNumber → label + skuId."""
        skus = self.client.subscribed_skus()
        return {
            part: {"label": EDUCATION_LICENSES[part], "sku_id": sku_id}
            for part, sku_id in skus.items()
            if part in EDUCATION_LICENSES
        }

    # -- Comptes cloud ------------------------------------------------------------------------

    def sync_users(
        self,
        plans: Sequence[CloudUserPlan],
        license_part: str = "",
        *,
        on_progress: Callable[[CloudUserPlan], None] | None = None,
    ) -> list[SyncResult]:
        """Crée les comptes absents de Entra ID et attribue la licence.

        Un compte déjà présent est signalé ``exists`` (jamais modifié) : la
        source de vérité pour les attributs reste l'AD / Entra Connect.
        """
        sku_id = ""
        if license_part:
            skus = self.client.subscribed_skus()
            sku_id = skus.get(license_part, "")
            if not sku_id:
                raise GraphError(
                    f"Licence absente du locataire : {license_part}. "
                    "Vérifiez les licences souscrites dans le portail Microsoft 365."
                )

        results: list[SyncResult] = []
        for plan in plans:
            if plan.existing:
                results.append(SyncResult(plan.upn, "exists", "compte déjà présent"))
            else:
                if not plan.password:
                    plan.password = generate_password(
                        cloud_password_policy(), prenom=plan.prenom, nom=plan.nom
                    )
                try:
                    user = self.client.create_user(
                        user_principal_name=plan.upn,
                        display_name=plan.display_name or plan.sam,
                        mail_nickname=mail_nickname(plan.sam),
                        password=plan.password,
                        given_name=plan.prenom,
                        surname=plan.nom,
                        account_enabled=not plan.disabled,
                        usage_location=self.config.usage_location,
                    )
                    detail = "compte créé"
                    if sku_id:
                        self.client.assign_license(str(user.get("id", "")), sku_id)
                        detail += " + licence attribuée"
                    results.append(SyncResult(plan.upn, "created", detail))
                except GraphError as exc:
                    results.append(SyncResult(plan.upn, "error", str(exc)))
            if on_progress:
                on_progress(plan)
        return results

    # -- Groupes / Teams ---------------------------------------------------------------------

    def cloud_indexes(self) -> tuple[dict[str, str], dict[str, str]]:
        """Index réutilisables pendant un lot : (UPN→id, mailNickname→id).

        Évite de re-interroger Graph à chaque item d'une batch d'import —
        les index sont figés au démarrage du lot.
        """
        users = {
            str(u.get("userPrincipalName", "")).lower(): str(u.get("id", ""))
            for u in self.client.list_users()
            if u.get("userPrincipalName")
        }
        groups = {
            str(g.get("mailNickname", "")).lower(): str(g.get("id", ""))
            for g in self.client.list_groups()
            if g.get("mailNickname")
        }
        return users, groups

    def sync_groups(
        self,
        plans: Sequence[GroupPlan],
        *,
        create_team: bool = False,
        users_by_upn: dict[str, str] | None = None,
        groups_by_nickname: dict[str, str] | None = None,
        on_progress: Callable[[GroupPlan], None] | None = None,
    ) -> list[SyncResult]:
        """Crée les groupes manquants et ajoute leurs membres (par UPN → id)."""
        users = users_by_upn if users_by_upn is not None else {
            str(u.get("userPrincipalName", "")).lower(): str(u.get("id", ""))
            for u in self.client.list_users()
            if u.get("userPrincipalName")
        }
        existing = groups_by_nickname if groups_by_nickname is not None else {
            str(g.get("mailNickname", "")).lower(): str(g.get("id", ""))
            for g in self.client.list_groups()
            if g.get("mailNickname")
        }

        results: list[SyncResult] = []
        for plan in plans:
            try:
                group_id = existing.get(plan.mail_nickname.lower(), "")
                created = False
                if not group_id:
                    group = self.client.create_group(
                        display_name=plan.name,
                        mail_nickname=plan.mail_nickname,
                        kind=plan.kind,
                    )
                    group_id = str(group.get("id", ""))
                    created = True

                added = 0
                for upn in plan.members:
                    user_id = users.get(upn.lower())
                    if not user_id:
                        continue
                    try:
                        self.client.add_group_member(group_id, user_id)
                        added += 1
                    except GraphError:
                        # AAD_DataConsistencyError : déjà membre — non bloquant
                        continue

                team = ""
                if created and plan.kind == "m365" and create_team:
                    self.client.create_team(group_id)
                    team = " + Teams créé"

                results.append(
                    SyncResult(
                        plan.name,
                        "created" if created else "exists",
                        f"groupe {'créé' if created else 'déjà présent'}, "
                        f"{added} membre(s) ajouté(s){team}",
                    )
                )
            except GraphError as exc:
                results.append(SyncResult(plan.name, "error", str(exc)))
            if on_progress:
                on_progress(plan)
        return results

    # -- Photos -------------------------------------------------------------------------------

    def sync_photos(
        self,
        mapping: dict[str, Path],
        *,
        users_by_upn: dict[str, str] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[SyncResult]:
        """Dépose chaque photo (recadrée/compressée comme en AD) comme photo de profil."""
        users = users_by_upn if users_by_upn is not None else {
            str(u.get("userPrincipalName", "")).lower(): str(u.get("id", ""))
            for u in self.client.list_users()
            if u.get("userPrincipalName")
        }

        results: list[SyncResult] = []
        for upn, path in mapping.items():
            try:
                user_id = users.get(upn.lower())
                if not user_id:
                    raise GraphError("compte introuvable dans Entra ID")
                photo = process_photo_from_path(Path(path))
                self.client.upload_user_photo(user_id, photo.data)
                results.append(
                    SyncResult(
                        upn, "updated",
                        f"photo {photo.width}×{photo.height} ({photo.size_kb:.0f} Ko)",
                    )
                )
            except (GraphError, PhotoError, OSError) as exc:
                results.append(SyncResult(upn, "error", str(exc)))
            if on_progress:
                on_progress(upn)
        return results


# -- Hybride (PHS / ADFS / PTA) ------------------------------------------------------------

def hybrid_checklist() -> list[tuple[str, str]]:
    """Étapes d'exploitation de l'identité hybride AD ↔ Entra ID.

    Graph ne peut pas configurer *Microsoft Entra Connect* : ces étapes sont
    manuelles, le module les documente au lieu de prétendre les piloter.
    """
    return [
        (
            "Synchronisation des mots de passe (PHS) — recommandé",
            "Installer Microsoft Entra Connect sur un serveur Windows joins au domaine, "
            "cocher « Synchronisation du mot de passe » dans l'assistant d'installation, "
            "puis vérifier dans Entra ID > Connecteurs de synchronisation > "
            "Détails du connecteur > Synchronisation des mots de passe : « Activé ».",
        ),
        (
            "Fédération (AD FS)",
            "Prévoir deux serveurs ADFS en haute disponibilité (load-balancer), "
            "certificat de fédération valide, et exécuter `Set-MsolDomainAuthentication` "
            "(ou l'assistant de fédération Entra Connect) pour basculer le domaine en fédéré. "
            "Inutile si PHS suffit à l'établissement.",
        ),
        (
            "Authentification transmise (PTA)",
            "Déployer 1 à 3 agents Pass-through Authentication sur des serveurs joins de domaine "
            "avec HTTPS sortant vers login.microsoftonline.com ; les mots de passe sont validés "
            "en direct auprès de l'AD sans les stocker dans le cloud.",
        ),
        (
            "Écriture retour des mots de passe (password writeback)",
            "Dans Entra Connect, activer l'écriture retour pour permettre la réinitialisation "
            "autoservice et l'expiration de mot de passe côté cloud — indispensable avec PHS/PTA.",
        ),
        (
            "Localisation d'usage & licences",
            "Chaque compte cloud doit porter un usageLocation (ISO 2, ex : FR) avant toute "
            "attribution de licence — c'est cette valeur que le module applique à la création.",
        ),
        (
            "Cohérence des identifiants",
            "L'UPN cloud reprend l'identifiant AD (samAccountName@domaine) : identique à celui "
            "créé par les modules de création de comptes, afin que PHS apparie les comptes "
            "sans créer de doublon."
        ),
    ]


def hybrid_checklist_markdown() -> str:
    """Rapport Markdown de la check-list hybride (exportable)."""
    lines = [
        "# Identité hybride AD ↔ Microsoft Entra ID (M19)",
        "",
        "Étapes manuelles — Microsoft Entra Connect ne peut pas être configuré "
        "depuis Microsoft Graph.",
        "",
    ]
    for index, (title, detail) in enumerate(hybrid_checklist(), start=1):
        lines.append(f"## {index}. {title}")
        lines.append("")
        lines.append(detail)
        lines.append("")
    return "\n".join(lines)
