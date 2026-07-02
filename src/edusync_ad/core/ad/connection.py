"""Connexion à l'Active Directory (§3 du cahier des charges).

`ADConnection` est une couche fine au-dessus de `ldap3`, volontairement sans
dépendance à PyQt6 pour rester testable hors UI. La connexion LDAPS est
tentée en priorité ; à défaut, une connexion LDAP en clair est établie avec
un avertissement explicite. Le mot de passe n'est jamais conservé au-delà de
l'appel à `connect()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ldap3 import ALL, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, SIMPLE, SUBTREE, BASE, Connection, Server
from ldap3.core.exceptions import LDAPException

from edusync_ad.core.ad.exceptions import (
    ADAuthError,
    ADCertificateError,
    ADError,
    ADInsufficientRightsError,
    ADUnreachableError,
)

logger = logging.getLogger("edusync_ad.ad")

ConnectionFactory = Callable[[str, str, str, bool], Connection]

UAC_NORMAL_ACCOUNT_ENABLED = 512
UAC_NORMAL_ACCOUNT_DISABLED = 514  # NORMAL_ACCOUNT | ACCOUNTDISABLE

# Évite un blocage indéfini de l'UI ("Connexion en cours…") quand le contrôleur
# de domaine est injoignable (mauvaise IP, pare-feu, réseau sandboxé Flatpak…).
CONNECT_TIMEOUT_SECONDS = 8


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


@dataclass
class ConnectResult:
    state: ConnectionState
    used_ldaps: bool
    warning: str | None = None


def default_connection_factory(
    controller: str, bind_user: str, password: str, use_ssl: bool
) -> Connection:
    server = Server(
        controller, use_ssl=use_ssl, get_info=ALL, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    return Connection(
        server,
        user=bind_user,
        password=password,
        authentication=SIMPLE,
        auto_bind=False,
        receive_timeout=CONNECT_TIMEOUT_SECONDS,
    )


class ADConnection:
    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._connection_factory = connection_factory or default_connection_factory
        self.state = ConnectionState.DISCONNECTED
        self.domain: str | None = None
        self.controller: str | None = None
        self.username: str | None = None
        self.used_ldaps: bool = False
        self.dry_run: bool = False
        self._conn: Connection | None = None

    @staticmethod
    def format_bind_user(domain: str, username: str) -> str:
        if "@" in username or "\\" in username:
            return username
        return f"{username}@{domain}"

    @staticmethod
    def domain_to_base_dn(domain: str) -> str:
        """Convertit un nom de domaine (ex. lycee-victor-hugo.local) en DN racine
        standard (ex. DC=lycee-victor-hugo,DC=local)."""
        return ",".join(f"DC={part}" for part in domain.split("."))

    def connect(self, domain: str, controller: str, username: str, password: str) -> ConnectResult:
        self.state = ConnectionState.CONNECTING
        self.domain, self.controller, self.username = domain, controller, username
        bind_user = self.format_bind_user(domain, username)

        warning: str | None = None
        try:
            self._conn = self._try_bind(controller, bind_user, password, use_ssl=True)
            self.used_ldaps = True
        except ADUnreachableError:
            try:
                self._conn = self._try_bind(controller, bind_user, password, use_ssl=False)
                self.used_ldaps = False
                warning = "Connexion LDAPS indisponible : repli sur LDAP non chiffré."
            except ADError:
                self.state = ConnectionState.DISCONNECTED
                raise
        except (ADAuthError, ADInsufficientRightsError, ADCertificateError):
            self.state = ConnectionState.DISCONNECTED
            raise

        self.state = ConnectionState.CONNECTED
        return ConnectResult(state=self.state, used_ldaps=self.used_ldaps, warning=warning)

    def _try_bind(
        self, controller: str, bind_user: str, password: str, *, use_ssl: bool
    ) -> Connection:
        try:
            conn = self._connection_factory(controller, bind_user, password, use_ssl)
        except LDAPException as exc:
            raise ADUnreachableError(f"Serveur injoignable : {exc}") from exc

        try:
            bound = conn.bind()
        except LDAPException as exc:
            raise ADUnreachableError(f"Serveur injoignable : {exc}") from exc

        if not bound:
            result = conn.result or {}
            description = (result.get("description") or "").lower()
            message = result.get("message") or "Connexion refusée par le serveur."
            if "invalidcredentials" in description:
                raise ADAuthError("Identifiant ou mot de passe incorrect.")
            if "insufficientaccessrights" in description:
                raise ADInsufficientRightsError("Droits insuffisants pour ce compte.")
            raise ADUnreachableError(message)
        return conn

    def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.unbind()
        self._conn = None
        self.state = ConnectionState.DISCONNECTED

    def _require_connected(self) -> Connection:
        if self.state != ConnectionState.CONNECTED or self._conn is None:
            raise ADError("Non connecté à l'Active Directory.")
        return self._conn

    # -- Lecture (toujours réelle, même en mode simulation) -----------------

    def search_existing_identifiers(self, base_dn: str) -> set[str]:
        conn = self._require_connected()
        if not conn.search(
            base_dn, "(sAMAccountName=*)", search_scope=SUBTREE, attributes=["sAMAccountName"]
        ):
            return set()
        return {
            str(entry["sAMAccountName"].value).lower()
            for entry in conn.entries
            if entry["sAMAccountName"].value
        }

    def ou_exists(self, ou_dn: str) -> bool:
        conn = self._require_connected()
        return bool(conn.search(ou_dn, "(objectClass=organizationalUnit)", search_scope=BASE))

    def group_exists(self, group_dn: str) -> bool:
        conn = self._require_connected()
        return bool(conn.search(group_dn, "(objectClass=group)", search_scope=BASE))

    # -- Écriture (court-circuitée en mode simulation) -----------------------

    def create_user(
        self,
        dn: str,
        attributes: dict,
        *,
        password: str | None = None,
        force_password_change: bool = False,
    ) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.add(dn, ["top", "person", "organizationalPerson", "user"], attributes):
            raise ADError(conn.result.get("description", "Échec de création du compte."))
        if password is not None:
            self.set_password(dn, password)
            self.enable_account(dn, force_password_change=force_password_change)

    def set_password(self, dn: str, password: str) -> None:
        """Définit le mot de passe AD via l'opération étendue Microsoft dédiée
        (nécessite une connexion chiffrée — LDAPS — sur un AD réel)."""
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.extend.microsoft.modify_password(dn, password):
            raise ADError(conn.result.get("description", "Échec de définition du mot de passe."))

    def enable_account(self, dn: str, *, force_password_change: bool = False) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        changes = {"userAccountControl": [(MODIFY_REPLACE, [UAC_NORMAL_ACCOUNT_ENABLED])]}
        if force_password_change:
            changes["pwdLastSet"] = [(MODIFY_REPLACE, [0])]
        if not conn.modify(dn, changes):
            raise ADError(conn.result.get("description", "Échec d'activation du compte."))

    def create_group(self, dn: str, sam_account_name: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        attributes = {"sAMAccountName": sam_account_name, "groupType": -2147483646}
        if not conn.add(dn, ["top", "group"], attributes):
            raise ADError(conn.result.get("description", "Échec de création du groupe."))

    def add_user_to_group(self, user_dn: str, group_dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify(group_dn, {"member": [(MODIFY_ADD, [user_dn])]}):
            raise ADError(conn.result.get("description", "Échec d'ajout au groupe."))

    def remove_user_from_group(self, user_dn: str, group_dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify(group_dn, {"member": [(MODIFY_DELETE, [user_dn])]}):
            raise ADError(conn.result.get("description", "Échec de suppression du groupe."))

    def search_user_by_cn(self, cn: str, ou_dn: str) -> str | None:
        """Retourne le DN du premier utilisateur avec ce CN dans l'OU, ou None."""
        conn = self._require_connected()
        if not conn.search(ou_dn, f"(cn={cn})", search_scope=SUBTREE, attributes=["cn"]):
            return None
        if not conn.entries:
            return None
        return str(conn.entries[0].entry_dn)

    def search_user_by_sam(self, sam_account_name: str, base_dn: str) -> tuple[str, str] | None:
        """Retourne (dn, cn) du premier utilisateur correspondant, ou None."""
        conn = self._require_connected()
        if not conn.search(
            base_dn,
            f"(sAMAccountName={sam_account_name})",
            search_scope=SUBTREE,
            attributes=["cn"],
        ):
            return None
        if not conn.entries:
            return None
        entry = conn.entries[0]
        return str(entry.entry_dn), str(entry["cn"].value)

    def move_user(self, user_dn: str, new_ou_dn: str) -> None:
        """Déplace un utilisateur vers une autre OU (modify_dn LDAP)."""
        conn = self._require_connected()
        if self.dry_run:
            return
        new_rdn = user_dn.split(",")[0]
        if not conn.modify_dn(user_dn, new_rdn, new_superior=new_ou_dn):
            raise ADError(conn.result.get("description", "Échec du déplacement du compte."))

    def disable_account(self, dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        changes = {"userAccountControl": [(MODIFY_REPLACE, [UAC_NORMAL_ACCOUNT_DISABLED])]}
        if not conn.modify(dn, changes):
            raise ADError(conn.result.get("description", "Échec de désactivation du compte."))

    def delete_user(self, dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.delete(dn):
            raise ADError(conn.result.get("description", "Échec de suppression du compte."))

    def search_user_groups(self, user_dn: str, base_dn: str) -> list[str]:
        """Retourne les DN des groupes dont l'utilisateur est membre."""
        conn = self._require_connected()
        if not conn.search(base_dn, f"(member={user_dn})", search_scope=SUBTREE, attributes=["cn"]):
            return []
        return [str(entry.entry_dn) for entry in conn.entries]

    # -- Module 5 : réinitialisation de mot de passe en masse -----------------

    def list_users_in_ou(self, ou_dn: str) -> list[dict]:
        """Retourne les attributs essentiels des utilisateurs d'une OU."""
        conn = self._require_connected()
        if not conn.search(
            ou_dn,
            "(&(objectClass=user)(objectCategory=person))",
            search_scope=SUBTREE,
            attributes=["sAMAccountName", "cn", "userAccountControl"],
        ):
            return []
        result = []
        for e in conn.entries:
            result.append({
                "dn": str(e.entry_dn),
                "sam": str(e["sAMAccountName"].value),
                "cn": str(e["cn"].value),
                "disabled": bool(int(e["userAccountControl"].value or 0) & 2),
            })
        return result

    def list_users_in_group(self, group_dn: str, base_dn: str) -> list[dict]:
        """Retourne les attributs essentiels des membres d'un groupe."""
        conn = self._require_connected()
        if not conn.search(group_dn, "(objectClass=group)", search_scope=BASE, attributes=["member"]):
            return []
        entries = conn.entries
        if not entries:
            return []
        members = entries[0]["member"].values or []
        result = []
        for member_dn in members:
            if not conn.search(
                str(member_dn),
                "(&(objectClass=user)(objectCategory=person))",
                search_scope=BASE,
                attributes=["sAMAccountName", "cn", "userAccountControl"],
            ):
                continue
            for e in conn.entries:
                result.append({
                    "dn": str(e.entry_dn),
                    "sam": str(e["sAMAccountName"].value),
                    "cn": str(e["cn"].value),
                    "disabled": bool(int(e["userAccountControl"].value or 0) & 2),
                })
        return result

    # -- Module 6 : explorateur AD -------------------------------------------

    def list_ous(self, base_dn: str) -> list[tuple[str, str]]:
        """Retourne (dn, nom) de toutes les OUs sous base_dn."""
        conn = self._require_connected()
        if not conn.search(base_dn, "(objectClass=organizationalUnit)", search_scope=SUBTREE, attributes=["ou"]):
            return []
        return [(str(e.entry_dn), str(e["ou"].value)) for e in conn.entries if e["ou"].value]

    def list_groups(self, base_dn: str) -> list[tuple[str, str]]:
        """Retourne (dn, cn) de tous les groupes sous base_dn."""
        conn = self._require_connected()
        if not conn.search(base_dn, "(objectClass=group)", search_scope=SUBTREE, attributes=["cn"]):
            return []
        return [(str(e.entry_dn), str(e["cn"].value)) for e in conn.entries if e["cn"].value]

    def get_user_attributes(self, user_dn: str) -> dict:
        """Retourne les attributs principaux d'un utilisateur."""
        conn = self._require_connected()
        attrs = [
            "sAMAccountName", "cn", "givenName", "sn", "displayName",
            "mail", "userAccountControl", "memberOf", "description",
            "telephoneNumber", "department", "title",
        ]
        if not conn.search(user_dn, "(objectClass=user)", search_scope=BASE, attributes=attrs):
            return {}
        if not conn.entries:
            return {}
        e = conn.entries[0]
        result: dict = {"dn": user_dn}
        for attr in attrs:
            try:
                val = e[attr].value
                result[attr] = str(val) if val is not None else ""
            except Exception:
                result[attr] = ""
        uac = int(e["userAccountControl"].value or 0)
        result["disabled"] = bool(uac & 2)
        result["memberOf"] = e["memberOf"].values or []
        return result

    def update_user_attribute(self, user_dn: str, attribute: str, value: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify(user_dn, {attribute: [(MODIFY_REPLACE, [value])]}):
            raise ADError(conn.result.get("description", f"Échec de modification de {attribute}."))

    def rename_user(self, user_dn: str, new_cn: str) -> None:
        """Renomme un utilisateur (modifie le CN/RDN)."""
        conn = self._require_connected()
        if self.dry_run:
            return
        new_rdn = f"CN={new_cn}"
        if not conn.modify_dn(user_dn, new_rdn):
            raise ADError(conn.result.get("description", "Échec du renommage."))
