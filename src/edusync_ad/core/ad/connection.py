"""Connexion à l'Active Directory (§3 du cahier des charges).

`ADConnection` est une couche fine au-dessus de `ldap3`, volontairement sans
dépendance à PyQt6 pour rester testable hors UI. La connexion LDAPS est
tentée en priorité ; à défaut, une connexion LDAP en clair est établie avec
un avertissement explicite. Le mot de passe n'est jamais conservé au-delà de
l'appel à `connect()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ldap3 import ALL, MODIFY_ADD, SIMPLE, SUBTREE, BASE, Connection, Server
from ldap3.core.exceptions import LDAPException

from edusync_ad.core.ad.exceptions import (
    ADAuthError,
    ADCertificateError,
    ADError,
    ADInsufficientRightsError,
    ADUnreachableError,
)

ConnectionFactory = Callable[[str, str, str, bool], Connection]


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
    server = Server(controller, use_ssl=use_ssl, get_info=ALL)
    return Connection(
        server, user=bind_user, password=password, authentication=SIMPLE, auto_bind=False
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

    def create_user(self, dn: str, attributes: dict) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.add(dn, ["top", "person", "organizationalPerson", "user"], attributes):
            raise ADError(conn.result.get("description", "Échec de création du compte."))

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
