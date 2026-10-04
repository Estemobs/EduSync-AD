"""M27 — Portail auto-service : configuration, stockage et logique métier.

Le portail laisse à un élève ou à un membre du personnel, depuis un simple
navigateur :

1. **réinitialiser son mot de passe** après vérification par un code à usage
   unique envoyé à son adresse de messagerie (``core/mailer.py``) ;
2. **consulter son identifiant et son adresse de messagerie** (même
   vérification) ;
3. **demander la création d'un compte** (pré-inscription), demande validée
   depuis l'application par un administrateur.

Choix de conception :

* **aucune dépendance externe** — ``smtplib``/``sqlite3`` de la bibliothèque
  standard, serveur HTTP dans ``core/portal_server.py`` ;
* **aucun secret lisible** : les codes sont stockés sous forme de dérivation
  PBKDF2 (sel aléatoire par ticket), à usage unique, avec expiration et
  plafond d'essais ;
* **pas d'énumération de comptes** : la réponse est identique que le compte
  existe ou non, que l'adresse soit renseignée ou non ;
* module **indépendant de PyQt** (testable hors écran) ; l'interface vit dans
  ``ui/portal_page.py``.

Hors périmètre volontaire (fournisseurs externes requis) : codes par SMS et
validation via Microsoft Authenticator — voir la section M27 de la roadmap.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog, data_dir, new_session_id
from edusync_ad.core.config import AppConfig, config_dir
from edusync_ad.core.identifiers import render_template
from edusync_ad.core.mailer import MailConfig, MailError, send_mail
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import generate_password, validate_password

PORTAL_CONFIG_FILE = config_dir() / "portail.json"
PORTAL_DB_PATH = data_dir() / "portail.db"

#: Longueur du code envoyé par mail (6 chiffres = 10^6 combinaisons).
CODE_LENGTH = 6
#: Fenêtre glissante de limitation des envois par identifiant.
THROTTLE_WINDOW_MINUTES = 15
#: Dérivation des codes (volontairement coûteuse : quelques dizaines de ms).
_PBKDF2_ITERATIONS = 60_000

#: Réponse unique, quel que soit le compte demandé (anti-énumération).
GENERIC_CODE_MESSAGE = (
    "Si un compte correspond, un code vient d'être envoyé à "
    "l'adresse de messagerie associée."
)

SCOPE_RESET = "reinitialisation"
SCOPE_LOOKUP = "consultation"
SCOPES = (SCOPE_RESET, SCOPE_LOOKUP)

PROFIL_ELEVE = "eleve"
PROFIL_PERSONNEL = "personnel"
PROFILS = (PROFIL_ELEVE, PROFIL_PERSONNEL)

STATUT_EN_ATTENTE = "en_attente"
STATUT_APPROUVEE = "approuvee"
STATUT_REFUSEE = "refusee"
STATUTS = (STATUT_EN_ATTENTE, STATUT_APPROUVEE, STATUT_REFUSEE)

POLITIQUE_LABELS = {"eleve": "Élèves", "personnel": "Personnels"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class PortalError(Exception):
    """Erreur métier du portail (message déjà en français)."""


# -- Configuration -----------------------------------------------------------


@dataclass
class PortalConfig:
    """Réglages du portail — ``portail.json`` (clair, aucun secret)."""

    #: Démarrage automatique du serveur en même temps que la session.
    actif: bool = False
    #: Interface d'écoute. 127.0.0.1 = machine de l'administrateur,
    #: 0.0.0.0 = tout le réseau interne.
    hote: str = "127.0.0.1"
    port: int = 8787
    #: Durée de validité d'un code.
    code_minutes: int = 10
    #: Nombre maximal de tentatives (bonnes ou mauvaises) par code.
    tentatives_max: int = 5
    #: Nombre maximal de codes demandés pour un même identifiant.
    envois_max: int = 3
    #: Politique de mot de passe appliquée aux réinitialisations.
    politique: str = PROFIL_ELEVE
    #: OU dans laquelle un compte est créé lors de la validation d'une demande.
    ou_accueil: str = ""
    #: Envoi des identifiants générés au moment de la validation.
    notification_mail: bool = True
    titre: str = "Portail auto-service"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (self.hote or "").strip():
            errors.append("Interface d'écoute requise (ex. 127.0.0.1).")
        if not (1 <= int(self.port) <= 65535):
            errors.append("Port invalide (1-65535).")
        if not (1 <= int(self.code_minutes) <= 60):
            errors.append("Durée de validité du code invalide (1-60 minutes).")
        if not (1 <= int(self.tentatives_max) <= 10):
            errors.append("Nombre de tentatives invalide (1-10).")
        if not (1 <= int(self.envois_max) <= 20):
            errors.append("Nombre d'envois maximal invalide (1-20).")
        if self.politique not in POLITIQUE_LABELS:
            errors.append(f"Politique inconnue : {self.politique}")
        if self.ou_accueil and "=" not in self.ou_accueil:
            errors.append(f"OU d'accueil invalide (DN attendu) : {self.ou_accueil}")
        return errors

    @property
    def base_url(self) -> str:
        host = self.hote.strip()
        display = "127.0.0.1" if host in ("0.0.0.0", "::", "*") else host
        return f"http://{display}:{int(self.port)}"


def load_portal_config(path: Path | None = None) -> PortalConfig:
    dest = path if path is not None else PORTAL_CONFIG_FILE
    if dest.exists():
        try:
            with dest.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                known = set(PortalConfig.__dataclass_fields__)
                return PortalConfig(**{key: value for key, value in data.items() if key in known})
        except (OSError, ValueError, TypeError):
            pass
    return PortalConfig()


def save_portal_config(config: PortalConfig, path: Path | None = None) -> Path:
    errors = config.validate()
    if errors:
        raise PortalError("Configuration du portail invalide : " + "; ".join(errors))
    dest = path if path is not None else PORTAL_CONFIG_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(asdict(config), fh, indent=2, ensure_ascii=False)
    return dest


# -- Compte lu dans l'annuaire ----------------------------------------------


@dataclass
class PortalUser:
    """Projection minimale d'un compte pour le portail."""

    dn: str
    sam: str
    cn: str = ""
    mail: str = ""
    disabled: bool = False

    @property
    def display_name(self) -> str:
        return self.cn or self.sam


# -- Demandes de création de compte -----------------------------------------


@dataclass
class Demande:
    id: str
    profil: str
    prenom: str
    nom: str
    mail: str = ""
    classe: str = ""
    motif: str = ""
    statut: str = STATUT_EN_ATTENTE
    created_at: str = ""
    traite_le: str = ""
    traite_par: str = ""
    note: str = ""

    @property
    def nom_complet(self) -> str:
        return f"{self.prenom} {self.nom}".strip()


# -- Stockage (SQLite, deux tables) -----------------------------------------


@dataclass
class _CodeRow:
    id: str
    scope: str
    cible: str
    user: PortalUser
    salt: bytes
    digest: bytes
    expires_at: datetime
    attempts: int
    consumed: bool


class PortalStore:
    """Tickets de vérification et demandes de compte.

    Une connexion unique protégée par un verrou : le serveur HTTP consulte ce
    magasin depuis plusieurs fils en même temps.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else PORTAL_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS codes (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    cible TEXT NOT NULL,
                    dn TEXT NOT NULL,
                    sam TEXT NOT NULL,
                    cn TEXT NOT NULL DEFAULT '',
                    mail TEXT NOT NULL DEFAULT '',
                    disabled INTEGER NOT NULL DEFAULT 0,
                    salt BLOB NOT NULL,
                    digest BLOB NOT NULL,
                    expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS codes_cible ON codes (cible);
                CREATE TABLE IF NOT EXISTS demandes (
                    id TEXT PRIMARY KEY,
                    profil TEXT NOT NULL,
                    prenom TEXT NOT NULL,
                    nom TEXT NOT NULL,
                    mail TEXT NOT NULL DEFAULT '',
                    classe TEXT NOT NULL DEFAULT '',
                    motif TEXT NOT NULL DEFAULT '',
                    statut TEXT NOT NULL DEFAULT 'en_attente',
                    created_at TEXT NOT NULL,
                    traite_le TEXT NOT NULL DEFAULT '',
                    traite_par TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._conn.commit()

    @staticmethod
    def _digest(code: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, _PBKDF2_ITERATIONS)

    # -- Codes ---------------------------------------------------------------

    def create_code(
        self,
        *,
        scope: str,
        cible: str,
        user: PortalUser,
        code: str,
        ttl_minutes: int,
        now: datetime | None = None,
    ) -> str:
        """Émet un ticket à usage unique. Retourne son identifiant."""
        moment = now or _now()
        salt = secrets.token_bytes(16)
        ticket = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO codes (id, scope, cible, dn, sam, cn, mail, disabled, "
                "salt, digest, expires_at, attempts, consumed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
                (
                    ticket,
                    scope,
                    cible,
                    user.dn,
                    user.sam,
                    user.cn,
                    user.mail,
                    1 if user.disabled else 0,
                    salt,
                    self._digest(code, salt),
                    _iso(moment + timedelta(minutes=int(ttl_minutes))),
                ),
            )
            # Purge des tickets expirés : la table ne grossit pas.
            self._conn.execute(
                "DELETE FROM codes WHERE expires_at < ?",
                (_iso(moment - timedelta(hours=1)),),
            )
            self._conn.commit()
        return ticket

    def count_recent_codes(self, cible: str, now: datetime | None = None) -> int:
        """Nombre de codes émis pour ``cible`` pendant la fenêtre de limitation."""
        moment = now or _now()
        since = _iso(moment - timedelta(minutes=THROTTLE_WINDOW_MINUTES))
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM codes WHERE cible = ? AND expires_at >= ?",
                (cible, since),
            ).fetchone()
        return int(row["n"])

    def _load_code(self, ticket: str) -> _CodeRow | None:
        row = self._conn.execute("SELECT * FROM codes WHERE id = ?", (ticket,)).fetchone()
        if row is None:
            return None
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return None
        return _CodeRow(
            id=row["id"],
            scope=row["scope"],
            cible=row["cible"],
            user=PortalUser(
                dn=row["dn"],
                sam=row["sam"],
                cn=row["cn"],
                mail=row["mail"],
                disabled=bool(row["disabled"]),
            ),
            salt=bytes(row["salt"]),
            digest=bytes(row["digest"]),
            expires_at=expires,
            attempts=int(row["attempts"]),
            consumed=bool(row["consumed"]),
        )

    def check_code(
        self, ticket: str, code: str, *, max_attempts: int, now: datetime | None = None
    ) -> tuple[bool, str, PortalUser | None]:
        """Vérifie un code **sans le consommer**.

        Retourne ``(valide, raison, compte)``. Chaque tentative est comptée,
        bonne ou mauvaise ; la raison détaillée ne sert qu'au journal.
        """
        moment = now or _now()
        with self._lock:
            row = self._load_code(ticket)
            if row is None:
                return False, "inconnu", None
            if row.consumed:
                return False, "consomme", None
            if row.expires_at <= moment:
                return False, "expire", None
            if row.attempts >= int(max_attempts):
                return False, "trop_de_tentatives", None

            self._conn.execute(
                "UPDATE codes SET attempts = attempts + 1 WHERE id = ?", (ticket,)
            )
            self._conn.commit()

            expected = self._digest(code or "", row.salt)
            if not hmac.compare_digest(expected, row.digest):
                return False, "code_incorrect", None
            return True, "ok", row.user

    def consume_code(self, ticket: str) -> bool:
        """Marque le ticket comme utilisé (appelé après le succès réel)."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE codes SET consumed = 1 WHERE id = ? AND consumed = 0", (ticket,)
            )
            self._conn.commit()
            return cursor.rowcount == 1

    # -- Demandes ------------------------------------------------------------

    def create_demande(
        self,
        *,
        profil: str,
        prenom: str,
        nom: str,
        mail: str = "",
        classe: str = "",
        motif: str = "",
        now: datetime | None = None,
    ) -> Demande:
        demande = Demande(
            id=uuid.uuid4().hex[:8],
            profil=profil,
            prenom=prenom.strip(),
            nom=nom.strip(),
            mail=mail.strip(),
            classe=classe.strip(),
            motif=motif.strip(),
            created_at=_iso(now or _now()),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO demandes (id, profil, prenom, nom, mail, classe, motif, "
                "statut, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    demande.id,
                    demande.profil,
                    demande.prenom,
                    demande.nom,
                    demande.mail,
                    demande.classe,
                    demande.motif,
                    demande.statut,
                    demande.created_at,
                ),
            )
            self._conn.commit()
        return demande

    @staticmethod
    def _demande_from_row(row: sqlite3.Row) -> Demande:
        return Demande(
            id=row["id"],
            profil=row["profil"],
            prenom=row["prenom"],
            nom=row["nom"],
            mail=row["mail"],
            classe=row["classe"],
            motif=row["motif"],
            statut=row["statut"],
            created_at=row["created_at"],
            traite_le=row["traite_le"],
            traite_par=row["traite_par"],
            note=row["note"],
        )

    def get_demande(self, demande_id: str) -> Demande | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM demandes WHERE id = ?", (demande_id,)
            ).fetchone()
        return self._demande_from_row(row) if row else None

    def list_demandes(self, statut: str | None = STATUT_EN_ATTENTE) -> list[Demande]:
        with self._lock:
            if statut:
                rows = self._conn.execute(
                    "SELECT * FROM demandes WHERE statut = ? ORDER BY created_at DESC",
                    (statut,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM demandes ORDER BY created_at DESC"
                ).fetchall()
        return [self._demande_from_row(row) for row in rows]

    def set_demande_statut(
        self,
        demande_id: str,
        statut: str,
        *,
        traite_par: str = "",
        note: str = "",
        now: datetime | None = None,
    ) -> Demande | None:
        with self._lock:
            self._conn.execute(
                "UPDATE demandes SET statut = ?, traite_le = ?, traite_par = ?, note = ? "
                "WHERE id = ?",
                (statut, _iso(now or _now()), traite_par, note, demande_id),
            )
            self._conn.commit()
        return self.get_demande(demande_id)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# -- Annuaire Active Directory ----------------------------------------------


class AdDirectory:
    """Adapter ``ADConnection`` ↔ portail (lecture, mot de passe, création)."""

    def __init__(self, ad_connection: ADConnection, domain: str = "") -> None:
        self._ad = ad_connection
        self.domain = domain or ad_connection.domain or ""
        self._base_dn = ADConnection.domain_to_base_dn(self.domain) if self.domain else ""

    def find_user(self, query: str) -> PortalUser | None:
        """Résout un identifiant ou une adresse de messagerie, ou ``None``."""
        value = (query or "").strip()
        if not value or not self._base_dn:
            return None
        try:
            if "@" in value:
                entries = self._ad.search_entries(
                    self._base_dn,
                    f"(mail={escape_filter_chars(value)})",
                    attributes=["sAMAccountName", "cn", "mail", "userAccountControl"],
                    size_limit=2,
                )
                if not entries:
                    return None
                entry = entries[0]
                return PortalUser(
                    dn=str(entry.get("dn") or ""),
                    sam=str(entry.get("sAMAccountName") or ""),
                    cn=str(entry.get("cn") or ""),
                    mail=str(entry.get("mail") or ""),
                    disabled=self._disabled_from(entry.get("userAccountControl")),
                )
            found = self._ad.search_user_by_sam(value, self._base_dn)
            if not found:
                return None
            dn, cn = found
            attrs = self._ad.get_user_attributes(dn)
            return PortalUser(
                dn=dn,
                sam=str(attrs.get("sAMAccountName") or value),
                cn=str(attrs.get("cn") or cn or ""),
                mail=str(attrs.get("mail") or ""),
                disabled=bool(attrs.get("disabled")),
            )
        except ADError:
            return None

    @staticmethod
    def _disabled_from(uac) -> bool:
        try:
            return bool(int(uac) & 0x2)
        except (TypeError, ValueError):
            return False

    def set_password(self, dn: str, password: str) -> None:
        """Écrit dans l'AD — le garde-fou RBAC de la session s'y applique."""
        self._ad.set_password(dn, password)

    def existing_identifiers(self) -> set[str]:
        return {
            str(name).lower()
            for name in self._ad.search_existing_identifiers(self._base_dn)
        }

    def create_account(self, *, dn: str, attributes: dict, password: str) -> None:
        self._ad.create_user(dn, attributes, password=password, force_password_change=True)


# -- Service -----------------------------------------------------------------


class PortalService:
    """Logique métier du portail, appelée par le serveur HTTP et par l'UI.

    Toutes les dépendances sont injectées (``directory``, ``sender``,
    ``store``, ``audit_path``, ``now``) : le service se teste sans LDAP, sans
    SMTP et sans écran.
    """

    def __init__(
        self,
        *,
        config: PortalConfig,
        app_config: AppConfig,
        directory,
        store: PortalStore | None = None,
        smtp_config: MailConfig | None = None,
        smtp_factory=None,
        sender=None,
        audit_path: Path | None = None,
        domain: str = "",
        now=None,
    ) -> None:
        self.config = config
        self.app_config = app_config
        self.directory = directory
        self.store = store if store is not None else PortalStore()
        self.smtp_config = smtp_config
        self.smtp_factory = smtp_factory
        self._sender = sender
        self.audit_path = audit_path
        self.domain = domain or getattr(directory, "domain", "")
        self._now = now or _now
        self._audit_lock = threading.RLock()
        self.session_id = new_session_id()

    # -- Journal d'audit -----------------------------------------------------
    # Le portail écrit depuis les fils du serveur HTTP : on ouvre une
    # connexion SQLite propre à chaque écriture (le journal de la session
    # appartient au fil de l'interface).

    def _audit(self, action: str, compte: str, resultat: str, detail: str = "") -> None:
        with self._audit_lock:
            log = AuditLog(self.audit_path)
            log.current_user = "portail"
            log.current_domain = self.domain
            try:
                log.record(action, compte, resultat, self.session_id, detail=detail)
            finally:
                log.close()

    # -- Envoi des codes -----------------------------------------------------

    def _send(self, to_addr: str, subject: str, body: str) -> None:
        if self._sender is not None:
            self._sender(to_addr, subject, body)
            return
        if self.smtp_config is None:
            raise MailError("Configuration SMTP renseignée requise.")
        send_mail(self.smtp_config, to_addr, subject, body, smtp_factory=self.smtp_factory)

    @staticmethod
    def _mask(mail: str) -> str:
        """``thomas.martin@ac-nantes.fr`` → ``…@ac-nantes.fr`` (journal)."""
        local, _, domain = (mail or "").partition("@")
        if not domain:
            return "-"
        return f"…@{domain}"

    def request_code(self, query: str, scope: str = SCOPE_RESET) -> tuple[str, str]:
        """Émet un code pour ``query``. Retourne ``(message, ticket)``.

        ``ticket`` est vide si aucun code n'a pu être émis — le message
        affiché reste identique dans tous les cas (anti-énumération), sauf
        pour la limitation de débit.
        """
        scope = scope if scope in SCOPES else SCOPE_RESET
        cible = (query or "").strip().lower()
        if not cible:
            return ("Saisissez votre identifiant ou votre adresse de messagerie.", "")

        if self.store.count_recent_codes(cible, now=self._now()) >= int(self.config.envois_max):
            self._audit("portail_code", "-", "echec", "limitation de débit")
            return ("Trop de demandes successives : réessayez dans quelques minutes.", "")

        user = self.directory.find_user(query)
        if user is None or not user.mail:
            detail = "compte introuvable" if user is None else "aucune adresse de messagerie"
            self._audit("portail_code", user.sam if user else "-", "echec", detail)
            return (GENERIC_CODE_MESSAGE, "")

        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        ticket = self.store.create_code(
            scope=scope,
            cible=cible,
            user=user,
            code=code,
            ttl_minutes=int(self.config.code_minutes),
            now=self._now(),
        )
        subject = f"{self.config.titre} — code de vérification"
        body = (
            "Bonjour,\n\n"
            f"Votre code de vérification est : {code}\n\n"
            f"Ce code expire dans {int(self.config.code_minutes)} minutes et ne peut "
            "être utilisé qu'une seule fois.\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez ce courriel "
            "et prévenez votre administrateur.\n\n"
            f"— {self.config.titre}"
        )
        try:
            self._send(user.mail, subject, body)
        except (MailError, OSError) as exc:
            self._audit("portail_code", user.sam, "echec", f"envoi impossible : {exc}")
            return ("L'envoi du code a échoué : contactez votre administrateur.", "")

        self._audit(
            "portail_code",
            user.sam,
            "succes",
            f"portee={scope}, dest={self._mask(user.mail)}",
        )
        return (GENERIC_CODE_MESSAGE, ticket)

    # -- Vérification --------------------------------------------------------

    def verify_code(
        self,
        ticket: str,
        code: str,
        *,
        scope: str = SCOPE_RESET,
        new_password: str = "",
    ) -> tuple[bool, str, dict | None]:
        """Vérifie le code puis réalise l'action demandée.

        Retourne ``(succès, message, informations)`` — ``informations``
        renseigné pour la consultation d'identifiants.
        """
        scope = scope if scope in SCOPES else SCOPE_RESET
        ok, reason, user = self.store.check_code(
            ticket,
            code,
            max_attempts=int(self.config.tentatives_max),
            now=self._now(),
        )
        if not ok or user is None:
            if reason in ("expire", "trop_de_tentatives"):
                # Le ticket est à bout : il faut en redemander un.
                self.store.consume_code(ticket)
            message = (
                "Trop de tentatives : demandez un nouveau code."
                if reason == "trop_de_tentatives"
                else "Ce code a déjà été utilisé : demandez un nouveau code."
                if reason == "consomme"
                else "Code incorrect ou expiré : demandez un nouveau code."
            )
            if reason != "inconnu":
                self._audit(
                    "portail_code",
                    user.sam if user else "-",
                    "echec",
                    f"verification={reason}",
                )
            return (False, message, None)

        if scope == SCOPE_LOOKUP:
            if not self.store.consume_code(ticket):
                return (False, "Ce code a déjà été utilisé : demandez un nouveau code.", None)
            self._audit("portail_consultation", user.sam, "succes", self._mask(user.mail))
            return (
                True,
                "Identifiants consultés.",
                {
                    "identifiant": user.sam,
                    "mail": user.mail,
                    "nom": user.display_name,
                },
            )

        # -- Réinitialisation -------------------------------------------------
        if user.disabled:
            self.store.consume_code(ticket)
            self._audit("portail_reinitialisation", user.sam, "echec", "compte désactivé")
            return (False, "Ce compte est désactivé : contactez votre administrateur.", None)

        errors = validate_password(
            self._password_policy(),
            new_password,
            forbidden=[user.sam, user.cn, user.display_name],
        )
        if errors:
            # Le code reste valable : l'usager corrige son mot de passe.
            return (False, " ".join(errors), {"erreurs": errors})

        if not self.store.consume_code(ticket):
            return (False, "Ce code a déjà été utilisé : demandez un nouveau code.", None)
        try:
            self.directory.set_password(user.dn, new_password)
        except ADError as exc:
            self._audit("portail_reinitialisation", user.sam, "echec", str(exc))
            return (
                False,
                "Impossible de modifier le mot de passe pour le moment : "
                "contactez votre administrateur.",
                None,
            )
        self._audit("portail_reinitialisation", user.sam, "succes", "auto-service")
        return (True, "Votre mot de passe a été modifié.", None)

    def _password_policy(self) -> PasswordPolicy:
        if self.config.politique == PROFIL_PERSONNEL:
            return self.app_config.politique_mdp_personnel
        return self.app_config.politique_mdp_eleve

    def policy_errors(self, password: str, *, sam: str = "", cn: str = "") -> list[str]:
        """Exigences d'un mot de passe choisi (affichage côté client)."""
        return validate_password(self._password_policy(), password, forbidden=[sam, cn])

    # -- Demandes de création de compte -------------------------------------

    def submit_request(self, payload: dict) -> tuple[bool, str]:
        profil = str(payload.get("profil") or "").strip()
        prenom = str(payload.get("prenom") or "").strip()
        nom = str(payload.get("nom") or "").strip()
        mail = str(payload.get("mail") or "").strip()
        classe = str(payload.get("classe") or "").strip()
        motif = str(payload.get("motif") or "").strip()

        if profil not in PROFILS:
            return (False, "Choisissez un profil (élève ou personnel).")
        if not prenom or not nom:
            return (False, "Le prénom et le nom sont obligatoires.")
        if mail and "@" not in mail:
            return (False, "L'adresse de messagerie saisie est invalide.")

        self.store.create_demande(
            profil=profil,
            prenom=prenom,
            nom=nom,
            mail=mail,
            classe=classe,
            motif=motif,
            now=self._now(),
        )
        self._audit(
            "portail_demande_compte", f"{prenom} {nom}".strip(), "succes", f"profil={profil}"
        )
        return (
            True,
            "Votre demande a été transmise à l'administrateur : vous recevrez "
            "les identifiants une fois le compte créé.",
        )

    def list_requests(self, statut: str | None = STATUT_EN_ATTENTE) -> list[Demande]:
        return self.store.list_demandes(statut)

    def approve_request(self, demande_id: str) -> dict:
        """Crée le compte demandé. Retourne les identifiants générés.

        Lève :class:`PortalError` avec un message affichable.
        """
        demande = self.store.get_demande(demande_id)
        if demande is None:
            raise PortalError("Cette demande n'existe plus.")
        if demande.statut != STATUT_EN_ATTENTE:
            raise PortalError("Cette demande a déjà été traitée.")
        if not (self.config.ou_accueil or "").strip():
            raise PortalError(
                "Renseignez l'OU d'accueil du portail avant de valider une demande."
            )

        prenom, nom = demande.prenom, demande.nom
        template = (
            self.app_config.identifiant_format_eleve
            if demande.profil == PROFIL_ELEVE
            else self.app_config.identifiant_format_personnel
        )
        sam = self._unique_identifier(render_template(template, prenom, nom))

        mail_domain = self.app_config.domaine_mail or self.domain
        mail = demande.mail or (
            f"{render_template(self.app_config.format_mail, prenom, nom)}@{mail_domain}"
        )
        policy = (
            self.app_config.politique_mdp_eleve
            if demande.profil == PROFIL_ELEVE
            else self.app_config.politique_mdp_personnel
        )
        password = generate_password(policy, prenom=prenom, nom=nom)

        cn = f"{prenom} {nom}".strip()
        dn = f"cn={escape_rdn(cn)},{self.config.ou_accueil}"
        attributes = {
            "sAMAccountName": sam,
            "userPrincipalName": f"{sam}@{self.domain}" if self.domain else sam,
            "givenName": prenom,
            "sn": nom,
            "displayName": cn,
            "mail": mail,
        }
        try:
            self.directory.create_account(dn=dn, attributes=attributes, password=password)
        except ADError as exc:
            raise PortalError(f"Création du compte impossible : {exc}") from exc

        self.store.set_demande_statut(
            demande_id, STATUT_APPROUVEE, traite_par="interface", now=self._now()
        )
        infos = {
            "dn": dn,
            "identifiant": sam,
            "mot_de_passe": password,
            "mail": mail,
            "nom": cn,
        }
        self._audit(
            "portail_demande_compte",
            cn,
            "succes",
            f"validation, identifiant={sam}",
        )
        if self.config.notification_mail and mail:
            self._notify_credentials(infos)
        return infos

    def _notify_credentials(self, infos: dict) -> None:
        """Transmission des identifiants générés (option ``notification_mail``).

        Un échec d'envoi ne remet jamais en cause la création du compte : il
        est seulement journalisé, l'administrateur pouvant communiquer les
        identifiants autrement.
        """
        body = (
            "Bonjour,\n\n"
            "Votre compte a été créé.\n\n"
            f"Identifiant : {infos.get('identifiant', '')}\n"
            f"Mot de passe : {infos.get('mot_de_passe', '')}\n"
            f"Adresse de messagerie : {infos.get('mail', '')}\n\n"
            "Ce mot de passe devra être changé à la première connexion.\n\n"
            f"— {self.config.titre}"
        )
        try:
            self._send(
                str(infos.get("mail") or ""),
                f"{self.config.titre} — vos identifiants",
                body,
            )
        except (MailError, OSError) as exc:
            self._audit(
                "portail_demande_compte",
                str(infos.get("identifiant") or "-"),
                "echec",
                f"notification impossible : {exc}",
            )
            return
        self._audit(
            "portail_demande_compte",
            str(infos.get("identifiant") or "-"),
            "succes",
            f"notification={self._mask(str(infos.get('mail') or ''))}",
        )

    def refuse_request(self, demande_id: str, note: str = "") -> None:
        demande = self.store.get_demande(demande_id)
        if demande is None:
            raise PortalError("Cette demande n'existe plus.")
        if demande.statut != STATUT_EN_ATTENTE:
            raise PortalError("Cette demande a déjà été traitée.")
        self.store.set_demande_statut(
            demande_id, STATUT_REFUSEE, traite_par="interface", note=note, now=self._now()
        )
        self._audit(
            "portail_demande_compte",
            demande.nom_complet,
            "succes",
            f"refus{f', note={note}' if note else ''}",
        )

    def _unique_identifier(self, base: str) -> str:
        base = (base or "").strip() or "compte"
        existing = self.directory.existing_identifiers()
        candidate = base
        suffix = 1
        while candidate.lower() in existing:
            suffix += 1
            if suffix > 100:
                raise PortalError("Impossible de générer un identifiant unique pour ce nom.")
            candidate = f"{base}{suffix}"
        return candidate
