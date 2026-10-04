"""Délégation d'administration — RBAC (M26 du cahier des charges).

Cinq rôles pour les opérateurs de l'application :

======================  =====================================================
Rôle                    Périmètre
======================  =====================================================
``super_admin``         Tout, y compris gérer les délégués et les paramètres
``admin_site``          Toutes les actions AD d'un site, sans gérer la délégation
``admin_classe``        Créer/migrer/réinitialiser/désactiver… **dans ses OU**
``helpdesk``            Consulter, réinitialiser un mot de passe, activer ou
                        désactiver un compte… **dans ses OU**
``lecture_seule``       Consulter et exporter, sans aucune écriture
======================  =====================================================

Trois briques :

* **Permissions** — vocabulaire hérité des métadonnées de plugins (T1), enfin
  *vérifié* : chaque écriture AD transite par ``ADConnection`` et le décorateur
  ``_logged_write``, qui demande ``policy.check(permission, dn…)``.
* **Portées (délégation par OU/groupe)** — un ``OperatorGrant`` associe un
  opérateur à un rôle, à un site éventuel (M25) et à des DN cibles. Les rôles
  ``admin_classe`` et ``helpdesk`` sont **scopés** : sans portée ils ne peuvent
  rien écrire, et toute cible doit être incluse dans leurs portées.
* **Résolution** — ``build_policy(opérateur, domaine)`` lit ``delegations.json``.
  Aucun délégué actif ⇒ tout le monde est ``super_admin`` (comportement historique
  conservé : l'outil tourne sur le poste de l'administrateur). Délégués présents
  mais opérateur inconnu ⇒ repli en ``lecture_seule`` (verrouillage doux, jamais
  de blocage total).

Le fichier ``delegations.json`` est en clair (il ne contient aucun secret, au
contraire de ``domaines.json``) mais il décrit qui peut faire quoi : il
protège contre les **erreurs d'un opérateur sur un poste partagé**, pas contre
un utilisateur local déterminé qui possède la machine et la clé ``secret.key``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from edusync_ad.core.ad.exceptions import ADInsufficientRightsError
from edusync_ad.core.config import config_dir

DELEGATIONS_FORMAT_VERSION = 1


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN_SITE = "admin_site"
    ADMIN_CLASSE = "admin_classe"
    HELPDESK = "helpdesk"
    LECTURE_SEULE = "lecture_seule"


ROLE_LABELS: dict[Role, str] = {
    Role.SUPER_ADMIN: "Super-admin",
    Role.ADMIN_SITE: "Admin site",
    Role.ADMIN_CLASSE: "Admin classe",
    Role.HELPDESK: "Helpdesk",
    Role.LECTURE_SEULE: "Lecture seule",
}

ROLE_ORDER: list[Role] = [
    Role.SUPER_ADMIN,
    Role.ADMIN_SITE,
    Role.ADMIN_CLASSE,
    Role.HELPDESK,
    Role.LECTURE_SEULE,
]

#: Vocabulaire de permissions (repris de ``ModuleMetadata.permissions``, T1).
#: Les clés sont celles vérifiées par ``ADConnection`` et par la navigation.
PERMISSIONS: dict[str, str] = {
    "read_ad": "Consulter l'annuaire (AD)",
    "read_user": "Consulter les comptes",
    "read_audit": "Consulter le journal d'actions",
    "export_data": "Exporter des données",
    "create_user": "Créer des comptes",
    "modify_user": "Modifier des comptes",
    "move_user": "Déplacer des comptes (migration)",
    "disable_account": "Activer / désactiver un compte",
    "delete_user": "Supprimer définitivement un compte",
    "reset_password": "Réinitialiser un mot de passe",
    "create_ou": "Créer ou renommer une OU",
    "delete_ou": "Supprimer une OU",
    "create_group": "Créer un groupe",
    "modify_group": "Modifier un groupe",
    "delete_group": "Supprimer un groupe",
    "manage_groups": "Gérer l'appartenance aux groupes",
    "modify_ad": "Modifier l'annuaire (explorateur, quotas, scripts)",
    "admin": "Administrer la délégation et les paramètres",
}

#: Permissions de lecture : exclusives des portées (tous les rôles lisent),
#: seules les écritures sont limitées aux OU/groupe délégués.
READ_PERMISSIONS: frozenset[str] = frozenset(
    {"read_ad", "read_user", "read_audit", "export_data"}
)

#: Rôles dont les actions sont restreintes aux portées du délégué.
SCOPED_ROLES: frozenset[Role] = frozenset({Role.ADMIN_CLASSE, Role.HELPDESK})


def _role_permissions(role: Role) -> frozenset[str]:
    every = frozenset(PERMISSIONS)
    if role is Role.SUPER_ADMIN:
        return every
    if role is Role.ADMIN_SITE:
        return every - {"admin"}
    if role is Role.ADMIN_CLASSE:
        return frozenset(
            {
                "read_ad", "read_user", "read_audit", "export_data", "modify_ad",
                "create_user", "modify_user", "move_user", "disable_account",
                "reset_password", "create_ou", "create_group", "modify_group",
                "manage_groups",
            }
        )
    if role is Role.HELPDESK:
        return frozenset(
            {
                "read_ad", "read_user", "read_audit", "export_data",
                "reset_password", "disable_account", "modify_user",
            }
        )
    return frozenset({"read_ad", "read_user", "read_audit", "export_data"})


#: Matrice rôle → permissions.
ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    role: _role_permissions(role) for role in ROLE_ORDER
}


def role_label(role: Role) -> str:
    return ROLE_LABELS.get(role, str(role))


# -- Portées -----------------------------------------------------------------

_DN_SPACES = re.compile(r"\s*=\s*")


def normalize_dn(dn: str) -> str:
    """DN en minuscules, sans espaces parasites autour des « = » et des virgules."""
    cleaned = _DN_SPACES.sub("=", dn or "")
    cleaned = re.sub(r"\s*,\s*", ",", cleaned)
    return cleaned.strip().lower()


def dn_in_scopes(dn: str, scopes: list[str] | tuple[str, ...]) -> bool:
    """True si ``dn`` est l'un des scopes ou vit à l'intérieur (sous-arbre).

    Une portée ``OU=3emeA,OU=eleves,DC=lycee,DC=local`` couvre donc tous les
    comptes, sous-OU et groupes qui y sont rattachés.
    """
    target = normalize_dn(dn)
    if not target:
        return False
    for scope in scopes:
        root = normalize_dn(scope)
        if not root:
            continue
        if target == root or target.endswith("," + root):
            return True
    return False


def unique_grant_id(existing: list[OperatorGrant] | None = None) -> str:
    taken = {grant.id for grant in (existing or [])}
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in taken:
            return candidate


# -- Délégués ---------------------------------------------------------------


@dataclass
class OperatorGrant:
    """Un opérateur, son rôle, le site concerné et ses portées."""

    id: str
    operateur: str
    role: Role = Role.LECTURE_SEULE
    #: Domaine AD (M25) ; vide = s'applique à tous les sites.
    site: str = ""
    #: DN d'OU : sous-arbre autorisé (écritures uniquement).
    ous: list[str] = field(default_factory=list)
    #: DN de groupe exactement gérés (appartenance, création/suppression).
    groupes: list[str] = field(default_factory=list)
    actif: bool = True

    @property
    def role_label(self) -> str:
        return role_label(self.role)

    @property
    def is_scoped(self) -> bool:
        return self.role in SCOPED_ROLES

    @property
    def scope_count(self) -> int:
        return len(self.ous) + len(self.groupes)

    def validate(self) -> list[str]:
        """Messages d'erreur (vide = délégué valide)."""
        errors: list[str] = []
        if not self.operateur.strip():
            errors.append("Le compte de l'opérateur est obligatoire.")
        if self.role not in ROLE_PERMISSIONS:
            errors.append(f"Rôle inconnu : {self.role}")
        if self.is_scoped and not (self.ous or self.groupes):
            errors.append(
                f"Le rôle « {self.role_label} » exige au moins une portée "
                "(OU ou groupe délégué)."
            )
        for dn in self.ous + self.groupes:
            if "=" not in dn:
                errors.append(f"Portée invalide (DN attendu) : {dn}")
        return errors


def _grant_from_dict(raw: dict) -> OperatorGrant:
    known = set(OperatorGrant.__dataclass_fields__)
    values = {key: value for key, value in raw.items() if key in known}
    values["id"] = values.get("id") or unique_grant_id()
    try:
        values["role"] = Role(values.get("role", Role.LECTURE_SEULE.value))
    except ValueError:
        values["role"] = Role.LECTURE_SEULE
    values["ous"] = [str(dn) for dn in values.get("ous", []) or []]
    values["groupes"] = [str(dn) for dn in values.get("groupes", []) or []]
    return OperatorGrant(**values)  # type: ignore[arg-type]


def delegations_path() -> Path:
    return config_dir() / "delegations.json"


def load_grants(path: Path | None = None) -> list[OperatorGrant]:
    """Charge les délégués. Un fichier illisible est ignoré (→ aucun délégué)."""
    path = path or delegations_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_grants = payload.get("grants", [])
        if not isinstance(raw_grants, list):
            return []
        return [_grant_from_dict(item) for item in raw_grants if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        return []


def save_grants(grants: list[OperatorGrant], path: Path | None = None) -> None:
    path = path or delegations_path()
    payload = {
        "version": DELEGATIONS_FORMAT_VERSION,
        "grants": [asdict(grant) for grant in grants],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def find_grant(grants: list[OperatorGrant], grant_id: str | None) -> OperatorGrant | None:
    if not grant_id:
        return None
    for grant in grants:
        if grant.id == grant_id:
            return grant
    return None


# -- Résolution opérateur → rôle --------------------------------------------


def _normalize_operator(value: str) -> str:
    """Accepte « admin », « LYCEE\\admin » et « admin@lycee.local »."""
    cleaned = (value or "").strip().lower()
    return cleaned.split("\\")[-1].split("@")[0]


def matching_grants(
    grants: list[OperatorGrant], operator: str, domain: str = ""
) -> list[OperatorGrant]:
    wanted = _normalize_operator(operator)
    site = (domain or "").strip().lower()
    return [
        grant
        for grant in grants
        if grant.actif
        and _normalize_operator(grant.operateur) == wanted
        and (not grant.site or grant.site.strip().lower() == site)
    ]


class RBACPolicy:
    """Décision pour un opérateur connecté à un site donné."""

    def __init__(
        self,
        operator: str,
        domain: str = "",
        role: Role = Role.SUPER_ADMIN,
        ous: list[str] | None = None,
        groupes: list[str] | None = None,
        source: str = "aucun délégué configuré",
    ) -> None:
        self.operator = operator
        self.domain = domain
        self.role = role
        self.ous: list[str] = list(ous or [])
        self.groupes: list[str] = list(groupes or [])
        self.source = source

    @classmethod
    def for_grants(
        cls, operator: str, domain: str = "", grants: list[OperatorGrant] | None = None
    ) -> "RBACPolicy":
        """Construit la politique de l'opérateur (voit le fichier si ``grants`` est None)."""
        if grants is None:
            grants = load_grants()
        active = [grant for grant in grants if grant.actif]
        if not active:
            # Rien n'a jamais été délégué : on conserve le fonctionnement
            # historique (poste d'administration à un seul responsable).
            return cls(
                operator,
                domain,
                Role.SUPER_ADMIN,
                source="aucun délégué configuré (rôle complet)",
            )

        matches = matching_grants(grants, operator, domain)
        if not matches:
            # Délégués existants mais opérateur non référencé : lecture seule,
            # jamais un blocage total (l'opérateur doit pouvoir consulter).
            return cls(
                operator,
                domain,
                Role.LECTURE_SEULE,
                source="opérateur non référencé dans la délégation",
            )

        # Un opérateur peut cumuler plusieurs délégués : on garde le rôle le
        # plus large (index le plus faible dans ROLE_ORDER) et l'union des
        # portées (les rôles non scopés ignorent ces dernières).
        best = min(matches, key=lambda grant: ROLE_ORDER.index(grant.role))
        ous: list[str] = []
        groupes: list[str] = []
        for grant in matches:
            ous.extend(dn for dn in grant.ous if dn not in ous)
            groupes.extend(dn for dn in grant.groupes if dn not in groupes)
        return cls(
            operator,
            domain,
            best.role,
            ous=ous,
            groupes=groupes,
            source="délégation : " + ", ".join(sorted(g.operateur for g in matches)),
        )

    # -- Décisions ----------------------------------------------------------

    @property
    def label(self) -> str:
        return role_label(self.role)

    @property
    def permissions(self) -> frozenset[str]:
        return ROLE_PERMISSIONS[self.role]

    @property
    def is_scoped(self) -> bool:
        return self.role in SCOPED_ROLES

    @property
    def readonly(self) -> bool:
        return self.role is Role.LECTURE_SEULE

    @property
    def can_manage_delegation(self) -> bool:
        return self.has("admin")

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def has_any(self, permissions) -> bool:
        return any(self.has(permission) for permission in permissions)

    def may(self, permission: str, *dns: str) -> bool:
        """Permission **et**, pour un rôle scopé, inclusion des cibles."""
        if not self.has(permission):
            return False
        targets = [dn for dn in dns if dn]
        if not targets or permission in READ_PERMISSIONS:
            return True
        if not self.is_scoped:
            return True
        if not (self.ous or self.groupes):
            # Rôle scopé sans portée : aucune écriture possible.
            return False
        return all(
            dn_in_scopes(dn, self.ous) or dn_in_scopes(dn, self.groupes)
            for dn in targets
        )

    def check(self, permission: str, *dns: str) -> None:
        """Lève :class:`ADInsufficientRightsError` si l'action est refusée."""
        label = PERMISSIONS.get(permission, permission)
        if not permission or permission not in PERMISSIONS:
            raise ADInsufficientRightsError(
                f"Action refusée « {label} » : cette action n'a pas de permission "
                "RBAC déclarée (verrouillage de sécurité)."
            )
        if not self.has(permission):
            raise ADInsufficientRightsError(
                f"Action refusée « {label} » : le rôle « {self.label} » de "
                f"« {self.operator or 'opérateur inconnu'} » ne l'autorise pas."
            )
        if not self.may(permission, *dns):
            raise ADInsufficientRightsError(
                f"Action refusée « {label} » : la cible est hors des OU/groupes "
                f"délégués à « {self.operator} » ({', '.join(self.ous + self.groupes) or 'aucune portée'})."
            )

    def describe(self) -> str:
        """Résumé affiché dans l'interface (rôle + portées)."""
        text = f"{self.label} — {self.source}"
        if self.is_scoped and (self.ous or self.groupes):
            text += f" · {len(self.ous)} OU, {len(self.groupes)} groupe(s)"
        return text


def build_policy(
    operator: str, domain: str = "", grants: list[OperatorGrant] | None = None
) -> RBACPolicy:
    """Politique de l'opérateur courant (lit ``delegations.json`` par défaut)."""
    return RBACPolicy.for_grants(operator, domain, grants)
