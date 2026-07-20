"""Connexion à l'Active Directory (§3 du cahier des charges).

`ADConnection` est une couche fine au-dessus de `ldap3`, volontairement sans
dépendance à PyQt6 pour rester testable hors UI. La connexion LDAPS est
tentée en priorité ; à défaut, une connexion LDAP en clair est établie avec
un avertissement explicite. Le mot de passe n'est jamais conservé au-delà de
l'appel à `connect()`.
"""

from __future__ import annotations

import functools
import logging
import ssl
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ldap3 import ALL, LEVEL, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, SIMPLE, SUBTREE, BASE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

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


def _locked(func):
    """Sérialise l'accès à la connexion ldap3 partagée entre threads.

    Une action par lot écrit depuis un QThread pendant que le thread principal
    peut lire au même moment (rafraîchir une liste d'OU, un autre module…).
    La stratégie SYNC par défaut de ldap3 n'est pas thread-safe : sans ce
    verrou, les réponses LDAP peuvent être attribuées à la mauvaise requête de
    façon aléatoire (bugs intermittents difficiles à reproduire)."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return func(self, *args, **kwargs)

    return wrapper


def _logged_write(action_desc: str):
    """Journalise chaque opération d'écriture AD (visible dans le menu Journal),
    pour comprendre en direct ce qui se passe pendant une action par lot.
    Sérialise aussi l'accès à la connexion (voir _locked) — le verrou est
    réentrant (RLock) car create_user rappelle set_password/enable_account,
    elles-mêmes décorées ici."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, dn, *args, **kwargs):
            logger.debug("%s : %s", action_desc, dn)
            with self._lock:
                try:
                    result = func(self, dn, *args, **kwargs)
                except Exception as exc:
                    logger.warning("Échec — %s (%s) : %s", action_desc, dn, exc)
                    raise
                else:
                    logger.debug("OK — %s : %s", action_desc, dn)
                    return result

        return wrapper

    return decorator


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
    controller: str,
    bind_user: str,
    password: str,
    use_ssl: bool,
    *,
    verify_certificate: bool = True,
    ca_cert_path: str | None = None,
) -> Connection:
    tls = None
    if use_ssl:
        # Par défaut, on exige un certificat valide (chaîne de confiance +
        # nom d'hôte) pour éviter qu'un LDAPS "chiffré" n'authentifie pas
        # réellement le contrôleur de domaine (interception possible sans
        # que rien ne le signale). Les AD internes utilisent presque toujours
        # un certificat émis par leur propre autorité (AD CS) : `ca_cert_path`
        # permet de pointer vers ce certificat racine exporté (.pem/.crt) sans
        # avoir à l'installer dans le magasin de confiance du poste.
        # `verify_certificate=False` reste disponible pour un dépannage
        # explicite (l'utilisateur doit l'activer sciemment), jamais par défaut.
        validate = ssl.CERT_REQUIRED if verify_certificate else ssl.CERT_NONE
        tls = Tls(validate=validate, ca_certs_file=ca_cert_path)
    server = Server(
        controller,
        use_ssl=use_ssl,
        tls=tls,
        get_info=ALL,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return Connection(
        server,
        user=bind_user,
        password=password,
        authentication=SIMPLE,
        auto_bind=False,
        receive_timeout=CONNECT_TIMEOUT_SECONDS,
        # Un AD renvoie parfois un référral vers son propre nom DNS (ex. lycee.local)
        # même pour une écriture locale. Si le poste client ne résout pas ce nom
        # (cas fréquent quand on se connecte par IP plutôt que via le DNS de l'AD),
        # ldap3 tente de le suivre et échoue avec "invalid server address". On
        # écrit toujours dans le contexte du serveur déjà joint : pas besoin de
        # suivre les référrals ici.
        auto_referrals=False,
    )


def _is_certificate_error(exc: BaseException) -> bool:
    """Détecte une erreur de validation de certificat TLS (chaîne de confiance
    ou nom d'hôte) au sein d'une exception ldap3, qui enveloppe l'erreur SSL
    d'origine sans exposer un type dédié."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        text = str(current).lower()
        if "certificate verify failed" in text or "certificate_verify_failed" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _raise_ad_error(conn: Connection, default_message: str) -> None:
    """Lève une ADError à partir du dernier résultat LDAP, avec un message
    explicite pour le cas "referral" (au lieu du mot brut, incompréhensible).

    Un AD renvoie un referral sur une écriture quand le client n'est pas
    connecté via le nom canonique du contrôleur pour cette partition — le cas
    classique est une connexion par adresse IP plutôt que par le nom DNS du
    contrôleur. auto_referrals=False (voir default_connection_factory) évite
    que ldap3 tente de suivre ce referral tout seul (ce qui provoquait un
    plantage "invalid server address" si ce nom n'est pas résolvable), mais
    l'écriture elle-même reste refusée tant que ce n'est pas corrigé.
    """
    result = conn.result or {}
    description = (result.get("description") or "").lower()
    if description == "referral":
        referrals = result.get("referrals") or []
        targets = ", ".join(referrals) if referrals else "un autre contrôleur de domaine"
        raise ADError(
            f"Le contrôleur de domaine refuse cette écriture et demande de la refaire vers : "
            f"{targets}. C'est un comportement AD classique quand on se connecte par adresse IP "
            "plutôt que par le nom DNS du contrôleur : configurez le DNS de ce poste pour résoudre "
            "le nom du domaine (ou renseignez ce nom, ex. dc01.lycee.local, comme « Contrôleur de "
            "domaine » au lieu de l'IP), puis reconnectez-vous."
        )
    raise ADError(result.get("description") or default_message)


def _format_pwd_last_set(value) -> str:
    """Formate l'attribut AD pwdLastSet en date lisible.

    AD ne stocke jamais le mot de passe en clair — seulement un hash non
    réversible — donc il est impossible d'afficher le mot de passe d'un
    compte existant. pwdLastSet indique seulement *quand* il a été changé.
    """
    from datetime import datetime, timedelta, timezone

    if value in (None, ""):
        return "Inconnu"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return "Inconnu"
    if raw == 0:
        return "Doit être changé à la prochaine connexion"
    # Filetime Windows : intervalles de 100 ns depuis 1601-01-01.
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return (epoch + timedelta(microseconds=raw / 10)).strftime("%Y-%m-%d %H:%M UTC")


class ADConnection:
    def __init__(
        self,
        connection_factory: ConnectionFactory | None = None,
        *,
        verify_certificate: bool = True,
        ca_cert_path: str | None = None,
    ) -> None:
        self._connection_factory = connection_factory or default_connection_factory
        self.state = ConnectionState.DISCONNECTED
        self.domain: str | None = None
        self.controller: str | None = None
        self.username: str | None = None
        self.used_ldaps: bool = False
        self.dry_run: bool = False
        # Validation du certificat LDAPS — voir default_connection_factory.
        # Sans effet si une connection_factory personnalisée est injectée
        # (tests, notamment), qui reçoit toujours les 4 arguments historiques.
        self.verify_certificate = verify_certificate
        self.ca_cert_path = ca_cert_path
        self._conn: Connection | None = None
        # Réentrant : create_user (déjà verrouillé) rappelle set_password et
        # enable_account, elles-mêmes verrouillées.
        self._lock = threading.RLock()

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

        logger.info("Connexion : domaine=%s, contrôleur=%s, utilisateur=%s", domain, controller, username)

        warning: str | None = None
        try:
            logger.debug("Tentative LDAPS (port 636, timeout %ss)…", CONNECT_TIMEOUT_SECONDS)
            self._conn = self._try_bind(controller, bind_user, password, use_ssl=True)
            self.used_ldaps = True
            logger.info("Connecté en LDAPS.")
        except ADUnreachableError as exc:
            logger.warning("LDAPS injoignable (%s) — repli sur LDAP non chiffré.", exc)
            try:
                self._conn = self._try_bind(controller, bind_user, password, use_ssl=False)
                self.used_ldaps = False
                warning = "Connexion LDAPS indisponible : repli sur LDAP non chiffré."
                logger.info("Connecté en LDAP (non chiffré).")
            except ADError as exc2:
                logger.error("Échec de connexion (LDAPS et LDAP) : %s", exc2)
                self.state = ConnectionState.DISCONNECTED
                raise
        except (ADAuthError, ADInsufficientRightsError, ADCertificateError) as exc:
            logger.error("Connexion refusée : %s", exc)
            self.state = ConnectionState.DISCONNECTED
            raise

        self.state = ConnectionState.CONNECTED
        return ConnectResult(state=self.state, used_ldaps=self.used_ldaps, warning=warning)

    def _try_bind(
        self, controller: str, bind_user: str, password: str, *, use_ssl: bool
    ) -> Connection:
        try:
            if self._connection_factory is default_connection_factory:
                conn = self._connection_factory(
                    controller,
                    bind_user,
                    password,
                    use_ssl,
                    verify_certificate=self.verify_certificate,
                    ca_cert_path=self.ca_cert_path,
                )
            else:
                conn = self._connection_factory(controller, bind_user, password, use_ssl)
        except LDAPException as exc:
            if _is_certificate_error(exc):
                raise ADCertificateError(
                    "Le certificat présenté par le contrôleur de domaine en LDAPS n'a pas pu être "
                    "validé (autorité inconnue ou nom d'hôte ne correspondant pas). Un AD interne "
                    "utilise presque toujours un certificat émis par sa propre autorité (AD CS) : "
                    "renseignez son certificat racine (.pem/.crt) dans les paramètres de connexion, "
                    "ou désactivez explicitement la vérification si vous acceptez le risque."
                ) from exc
            raise ADUnreachableError(f"Serveur injoignable : {exc}") from exc

        try:
            logger.debug("Envoi de la requête bind à %s…", controller)
            bound = conn.bind()
        except LDAPException as exc:
            if _is_certificate_error(exc):
                raise ADCertificateError(
                    "Le certificat présenté par le contrôleur de domaine en LDAPS n'a pas pu être "
                    "validé (autorité inconnue ou nom d'hôte ne correspondant pas). Un AD interne "
                    "utilise presque toujours un certificat émis par sa propre autorité (AD CS) : "
                    "renseignez son certificat racine (.pem/.crt) dans les paramètres de connexion, "
                    "ou désactivez explicitement la vérification si vous acceptez le risque."
                ) from exc
            raise ADUnreachableError(f"Serveur injoignable ou délai dépassé : {exc}") from exc

        if not bound:
            result = conn.result or {}
            description = (result.get("description") or "").lower()
            message = result.get("message") or "Connexion refusée par le serveur."
            logger.debug("Bind refusé : %s (%s)", description, message)
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

    @_locked
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

    @_locked
    def ou_exists(self, ou_dn: str) -> bool:
        conn = self._require_connected()
        return bool(conn.search(ou_dn, "(objectClass=organizationalUnit)", search_scope=BASE))

    @_locked
    def group_exists(self, group_dn: str) -> bool:
        conn = self._require_connected()
        return bool(conn.search(group_dn, "(objectClass=group)", search_scope=BASE))

    # -- Écriture (court-circuitée en mode simulation) -----------------------

    @_logged_write("Création du compte")
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
            _raise_ad_error(conn, "Échec de création du compte.")
        if password is not None:
            try:
                self.set_password(dn, password)
                self.enable_account(dn, force_password_change=force_password_change)
            except ADError:
                # Le compte a bien été créé dans l'AD (add() ci-dessus a réussi) mais
                # reste désactivé et sans mot de passe si la suite échoue — on le
                # supprime pour rester atomique plutôt que de laisser un compte
                # orphelin invisible pour l'opérateur (qui ne verrait qu'un « échec »).
                logger.warning(
                    "Mot de passe/activation en échec juste après la création — "
                    "suppression du compte orphelin : %s", dn,
                )
                try:
                    conn.delete(dn)
                except LDAPException:
                    logger.warning(
                        "Impossible de supprimer le compte orphelin %s — intervention manuelle requise.", dn,
                    )
                raise

    @_logged_write("Définition du mot de passe")
    def set_password(self, dn: str, password: str) -> None:
        """Définit le mot de passe AD via l'opération étendue Microsoft dédiée
        (nécessite une connexion chiffrée — LDAPS — sur un AD réel)."""
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.extend.microsoft.modify_password(dn, password):
            if not self.used_ldaps:
                # AD refuse quasi systématiquement de modifier un mot de passe
                # sur une connexion LDAP non chiffrée (port 389) — un message
                # d'erreur brut LDAP serait cryptique ici.
                raise ADError(
                    "Le changement de mot de passe nécessite une connexion chiffrée (LDAPS, "
                    "port 636). Vous êtes connecté en LDAP non chiffré — configurez un "
                    "certificat LDAPS sur le contrôleur de domaine (ou vérifiez que le port "
                    "636 est accessible), puis reconnectez-vous."
                )
            _raise_ad_error(conn, "Échec de définition du mot de passe.")

    @_logged_write("Activation du compte")
    def enable_account(self, dn: str, *, force_password_change: bool = False) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        changes = {"userAccountControl": [(MODIFY_REPLACE, [UAC_NORMAL_ACCOUNT_ENABLED])]}
        if force_password_change:
            changes["pwdLastSet"] = [(MODIFY_REPLACE, [0])]
        if not conn.modify(dn, changes):
            result = conn.result or {}
            if (result.get("description") or "").lower() == "unwillingtoperform":
                # Cas classique : AD refuse d'activer un compte qui n'a jamais eu de
                # mot de passe défini (contrainte de sécurité du contrôleur de domaine,
                # indépendante des droits de l'opérateur) — message brut sinon cryptique.
                raise ADError(
                    "Le contrôleur de domaine refuse d'activer ce compte : Active Directory "
                    "n'autorise pas l'activation d'un compte sans mot de passe défini. "
                    "Définissez d'abord un mot de passe (réinitialisation) avant de l'activer."
                )
            _raise_ad_error(conn, "Échec d'activation du compte.")

    @_logged_write("Création de l'OU")
    def create_ou(self, dn: str, name: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.add(dn, ["top", "organizationalUnit"], {"ou": name}):
            _raise_ad_error(conn, "Échec de création de l'OU.")

    @_locked
    def ou_is_empty(self, ou_dn: str) -> bool:
        """Vérifie qu'une OU ne contient aucun objet direct (protection
        contre une suppression accidentelle d'une OU encore utilisée)."""
        conn = self._require_connected()
        if not conn.search(ou_dn, "(objectClass=*)", search_scope=LEVEL):
            return True
        # Filtre défensif : certaines implémentations (dont MOCK_SYNC) incluent
        # parfois la base elle-même en scope LEVEL, ce qui ne devrait pas arriver.
        children = [e for e in conn.entries if str(e.entry_dn).lower() != ou_dn.lower()]
        return not children

    @_logged_write("Suppression de l'OU")
    def delete_ou(self, dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.delete(dn):
            _raise_ad_error(conn, "Échec de suppression de l'OU.")

    @_logged_write("Renommage de l'OU")
    def rename_ou(self, dn: str, new_name: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify_dn(dn, f"OU={escape_rdn(new_name)}"):
            _raise_ad_error(conn, "Échec du renommage de l'OU.")

    @_logged_write("Création du groupe")
    def create_group(self, dn: str, sam_account_name: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        attributes = {"sAMAccountName": sam_account_name, "groupType": -2147483646}
        if not conn.add(dn, ["top", "group"], attributes):
            _raise_ad_error(conn, "Échec de création du groupe.")

    @_logged_write("Suppression du groupe")
    def delete_group(self, dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.delete(dn):
            _raise_ad_error(conn, "Échec de suppression du groupe.")

    @_logged_write("Ajout au groupe")
    def add_user_to_group(self, user_dn: str, group_dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify(group_dn, {"member": [(MODIFY_ADD, [user_dn])]}):
            _raise_ad_error(conn, "Échec d'ajout au groupe.")

    @_logged_write("Retrait du groupe")
    def remove_user_from_group(self, user_dn: str, group_dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify(group_dn, {"member": [(MODIFY_DELETE, [user_dn])]}):
            _raise_ad_error(conn, "Échec de suppression du groupe.")

    @_locked
    def search_user_by_cn(self, cn: str, ou_dn: str) -> str | None:
        """Retourne le DN du premier utilisateur avec ce CN dans l'OU, ou None."""
        conn = self._require_connected()
        if not conn.search(
            ou_dn, f"(cn={escape_filter_chars(cn)})", search_scope=SUBTREE, attributes=["cn"]
        ):
            return None
        if not conn.entries:
            return None
        return str(conn.entries[0].entry_dn)

    @_locked
    def search_user_by_sam(self, sam_account_name: str, base_dn: str) -> tuple[str, str] | None:
        """Retourne (dn, cn) du premier utilisateur correspondant, ou None."""
        conn = self._require_connected()
        if not conn.search(
            base_dn,
            f"(sAMAccountName={escape_filter_chars(sam_account_name)})",
            search_scope=SUBTREE,
            attributes=["cn"],
        ):
            return None
        if not conn.entries:
            return None
        entry = conn.entries[0]
        return str(entry.entry_dn), str(entry["cn"].value)

    @_logged_write("Déplacement du compte")
    def move_user(self, user_dn: str, new_ou_dn: str) -> None:
        """Déplace un utilisateur vers une autre OU (modify_dn LDAP)."""
        conn = self._require_connected()
        if self.dry_run:
            return
        new_rdn = user_dn.split(",")[0]
        if not conn.modify_dn(user_dn, new_rdn, new_superior=new_ou_dn):
            _raise_ad_error(conn, "Échec du déplacement du compte.")

    @_logged_write("Désactivation du compte")
    def disable_account(self, dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        changes = {"userAccountControl": [(MODIFY_REPLACE, [UAC_NORMAL_ACCOUNT_DISABLED])]}
        if not conn.modify(dn, changes):
            _raise_ad_error(conn, "Échec de désactivation du compte.")

    @_logged_write("Suppression du compte")
    def delete_user(self, dn: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.delete(dn):
            _raise_ad_error(conn, "Échec de suppression du compte.")

    @_locked
    def search_user_groups(self, user_dn: str, base_dn: str) -> list[str]:
        """Retourne les DN des groupes dont l'utilisateur est membre."""
        conn = self._require_connected()
        if not conn.search(
            base_dn, f"(member={escape_filter_chars(user_dn)})", search_scope=SUBTREE, attributes=["cn"]
        ):
            return []
        return [str(entry.entry_dn) for entry in conn.entries]

    # -- Module 5 : réinitialisation de mot de passe en masse -----------------

    @_locked
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

    @_locked
    def list_users_in_group(self, group_dn: str, base_dn: str) -> list[dict]:
        """Retourne les attributs essentiels des membres d'un groupe.

        Une seule requête filtrée sur `memberOf` (backlink AD maintenu
        automatiquement) au lieu d'une recherche par membre (N+1 requêtes,
        lent sur un gros groupe)."""
        conn = self._require_connected()
        if not conn.search(
            base_dn,
            f"(&(objectClass=user)(objectCategory=person)(memberOf={escape_filter_chars(group_dn)}))",
            search_scope=SUBTREE,
            attributes=["sAMAccountName", "cn", "userAccountControl"],
        ):
            return []
        return [
            {
                "dn": str(e.entry_dn),
                "sam": str(e["sAMAccountName"].value),
                "cn": str(e["cn"].value),
                "disabled": bool(int(e["userAccountControl"].value or 0) & 2),
            }
            for e in conn.entries
        ]

    # -- Module 6 : explorateur AD -------------------------------------------

    @_locked
    def list_ous(self, base_dn: str) -> list[tuple[str, str]]:
        """Retourne (dn, nom) de toutes les OUs sous base_dn."""
        conn = self._require_connected()
        if not conn.search(base_dn, "(objectClass=organizationalUnit)", search_scope=SUBTREE, attributes=["ou"]):
            return []
        return [(str(e.entry_dn), str(e["ou"].value)) for e in conn.entries if e["ou"].value]

    @_locked
    def list_groups(self, base_dn: str) -> list[tuple[str, str]]:
        """Retourne (dn, cn) de tous les groupes sous base_dn."""
        conn = self._require_connected()
        if not conn.search(base_dn, "(objectClass=group)", search_scope=SUBTREE, attributes=["cn"]):
            return []
        return [(str(e.entry_dn), str(e["cn"].value)) for e in conn.entries if e["cn"].value]

    @_locked
    def get_user_attributes(self, user_dn: str) -> dict:
        """Retourne les attributs principaux d'un utilisateur.

        Ne contient jamais le mot de passe : Active Directory ne le stocke
        que sous forme de hash non réversible (unicodePwd est un attribut en
        écriture seule), il est donc impossible de l'afficher pour un compte
        existant — quel que soit l'outil utilisé.
        """
        conn = self._require_connected()
        attrs = [
            "sAMAccountName", "cn", "givenName", "sn", "displayName",
            "mail", "userAccountControl", "memberOf", "description",
            "telephoneNumber", "department", "title", "pwdLastSet",
        ]
        if not conn.search(user_dn, "(objectClass=user)", search_scope=BASE, attributes=attrs):
            return {}
        if not conn.entries:
            return {}
        e = conn.entries[0]
        result: dict = {"dn": user_dn}
        for attr in attrs:
            if attr == "pwdLastSet":
                continue
            try:
                val = e[attr].value
                result[attr] = str(val) if val is not None else ""
            except Exception:
                result[attr] = ""
        uac = int(e["userAccountControl"].value or 0)
        result["disabled"] = bool(uac & 2)
        result["memberOf"] = e["memberOf"].values or []
        result["dernier_changement_mdp"] = _format_pwd_last_set(e["pwdLastSet"].value)
        return result

    @_logged_write("Modification d'attribut")
    def update_user_attribute(self, user_dn: str, attribute: str, value: str) -> None:
        conn = self._require_connected()
        if self.dry_run:
            return
        if not conn.modify(user_dn, {attribute: [(MODIFY_REPLACE, [value])]}):
            _raise_ad_error(conn, f"Échec de modification de {attribute}.")

    @_logged_write("Renommage du compte")
    def rename_user(self, user_dn: str, new_cn: str) -> None:
        """Renomme un utilisateur (modifie le CN/RDN)."""
        conn = self._require_connected()
        if self.dry_run:
            return
        new_rdn = f"CN={escape_rdn(new_cn)}"
        if not conn.modify_dn(user_dn, new_rdn):
            _raise_ad_error(conn, "Échec du renommage.")
