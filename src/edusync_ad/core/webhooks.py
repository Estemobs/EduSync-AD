"""M28 — Système de Webhooks (notifications événementielles).

Les webhooks permettent d'être notifié en temps réel des événements :
- compte créé / migré / désactivé / archivé / supprimé
- mot de passe réinitialisé
- demande portail validée / refusée
- etc.

Chaque webhook définit :
- URL cible (HTTPS recommandé)
- Événements abonnés (liste ou "*")
- Secret HMAC pour vérification de signature
- Headers personnalisés optionnels
- Politique de retry (backoff exponentiel)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import asdataclass, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from edusync_ad.core.config import config_dir

try:
    import urllib.request
except ImportError:  # pragma: no cover - stdlib
    urllib = None  # type: ignore

WEBHOOKS_FILE = config_dir() / "webhooks.json"
WEBHOOK_DELIVERIES_FILE = config_dir() / "webhook_deliveries.db"

logger = logging.getLogger("edusync_ad.webhooks")


class WebhookEvent(str, Enum):
    """Événements standards — extensibles."""

    # Utilisateurs
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    USER_MOVED = "user.moved"
    USER_DISABLED = "user.disabled"
    USER_ENABLED = "user.enabled"
    USER_PASSWORD_RESET = "user.password_reset"

    # Groupes
    GROUP_CREATED = "group.created"
    GROUP_UPDATED = "group.updated"
    GROUP_DELETED = "group.deleted"
    GROUP_MEMBER_ADDED = "group.member_added"
    GROUP_MEMBER_REMOVED = "group.member_removed"

    # OUs
    OU_CREATED = "ou.created"
    OU_RENAMED = "ou.renamed"
    OU_DELETED = "ou.deleted"

    # Portail
    PORTAL_CODE_SENT = "portal.code_sent"
    PORTAL_PASSWORD_RESET = "portal.password_reset"
    PORTAL_LOOKUP = "portal.lookup"
    PORTAL_REQUEST_SUBMITTED = "portal.request_submitted"
    PORTAL_REQUEST_APPROVED = "portal.request_approved"
    PORTAL_REQUEST_REFUSED = "portal.request_refused"

    # Audit
    AUDIT_ENTRY = "audit.entry"

    # Système
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"

    @classmethod
    def all_events(cls) -> list[str]:
        return [e.value for e in cls]

    @classmethod
    def user_events(cls) -> list[str]:
        return [e.value for e in cls if e.value.startswith("user.")]

    @classmethod
    def group_events(cls) -> list[str]:
        return [e.value for e in cls if e.value.startswith("group.")]

    @classmethod
    def portal_events(cls) -> list[str]:
        return [e.value for e in cls if e.value.startswith("portal.")]


@dataclass
class WebhookConfig:
    """Configuration d'un webhook (stockée, secret chiffré)."""

    id: str
    name: str
    url: str
    events: list[str]  # ["user.created", "*"] — "*" = tous
    secret: str = ""  # stocké chiffré via crypto.py
    headers: dict[str, str] = field(default_factory=dict)
    active: bool = True
    created_at: str = ""
    last_triggered_at: str | None = None
    last_status: int | None = None
    last_error: str | None = None
    retry_policy: str = "exponential"  # "exponential" | "fixed" | "none"
    max_retries: int = 5
    timeout_seconds: float = 10.0

    def validate(self) -> list[str]:
        errors = []
        if not self.name.strip():
            errors.append("Nom requis")
        if not self.url.strip():
            errors.append("URL requise")
        else:
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https"):
                errors.append("URL doit être http:// ou https://")
            if not parsed.netloc:
                errors.append("URL invalide (hôte manquant)")
        if not self.events:
            errors.append("Au moins un événement requis")
        for ev in self.events:
            if ev != "*" and ev not in WebhookEvent.all_events():
                errors.append(f"Événement inconnu : {ev}")
        if self.max_retries < 0 or self.max_retries > 20:
            errors.append("Max retries invalide (0-20)")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            errors.append("Timeout invalide (1-60s)")
        return errors


@dataclass
class WebhookDelivery:
    """Tentative de livraison (journal)."""

    id: str
    webhook_id: str
    event: str
    payload: dict
    attempt: int
    status_code: int | None
    response_body: str | None
    error: str | None
    started_at: str
    completed_at: str | None = None
    next_retry_at: str | None = None


class WebhookStore:
    """Persistance webhooks + journal des livraisons (SQLite)."""

    def __init__(
        self,
        config_path: Path | None = None,
        deliveries_path: Path | None = None,
    ) -> None:
        self.config_path = config_path if config_path is not None else WEBHOOKS_FILE
        self.deliveries_path = deliveries_path if deliveries_path is not None else WEBHOOK_DELIVERIES_FILE
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.deliveries_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._webhooks: dict[str, WebhookConfig] = {}
        self._init_db()
        self._load()

    def _init_db(self) -> None:
        import sqlite3

        with self._lock:
            conn = sqlite3.connect(self.deliveries_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    webhook_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status_code INTEGER,
                    response_body TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    next_retry_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_deliveries_webhook ON deliveries (webhook_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries (next_retry_at) WHERE next_retry_at IS NOT NULL"
            )
            conn.commit()
            conn.close()

    def _load(self) -> None:
        if not self.config_path.exists():
            return
        try:
            with self.config_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        wh = WebhookConfig(**item)
                        self._webhooks[wh.id] = wh
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        with self._lock:
            data = [asdataclass(w) for w in self._webhooks.values()]
            with self.config_path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

    def create(self, config: WebhookConfig) -> WebhookConfig:
        errors = config.validate()
        if errors:
            raise ValueError("; ".join(errors))
        with self._lock:
            self._webhooks[config.id] = config
            self._save()
        return config

    def get(self, webhook_id: str) -> Optional[WebhookConfig]:
        with self._lock:
            return self._webhooks.get(webhook_id)

    def list(self, active_only: bool = False) -> list[WebhookConfig]:
        with self._lock:
            whs = list(self._webhooks.values())
        if active_only:
            whs = [w for w in whs if w.active]
        return whs

    def update(self, webhook_id: str, **changes) -> Optional[WebhookConfig]:
        with self._lock:
            wh = self._webhooks.get(webhook_id)
            if not wh:
                return None
            for key, value in changes.items():
                if hasattr(wh, key):
                    setattr(wh, key, value)
            errors = wh.validate()
            if errors:
                raise ValueError("; ".join(errors))
            self._save()
            return wh

    def delete(self, webhook_id: str) -> bool:
        with self._lock:
            if webhook_id in self._webhooks:
                del self._webhooks[webhook_id]
                self._save()
                return True
        return False

    def log_delivery(self, delivery: WebhookDelivery) -> None:
        import sqlite3

        with self._lock:
            conn = sqlite3.connect(self.deliveries_path)
            conn.execute(
                """
                INSERT INTO deliveries (id, webhook_id, event, payload, attempt,
                    status_code, response_body, error, started_at, completed_at, next_retry_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.id,
                    delivery.webhook_id,
                    delivery.event,
                    json.dumps(delivery.payload, ensure_ascii=False),
                    delivery.attempt,
                    delivery.status_code,
                    delivery.response_body,
                    delivery.error,
                    delivery.started_at,
                    delivery.completed_at,
                    delivery.next_retry_at,
                ),
            )
            conn.commit()
            conn.close()

    def get_pending_retries(self, limit: int = 50) -> list[WebhookDelivery]:
        import sqlite3

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            conn = sqlite3.connect(self.deliveries_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM deliveries
                WHERE next_retry_at IS NOT NULL AND next_retry_at <= ?
                ORDER BY next_retry_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            conn.close()
        return [
            WebhookDelivery(
                id=r["id"],
                webhook_id=r["webhook_id"],
                event=r["event"],
                payload=json.loads(r["payload"]),
                attempt=r["attempt"],
                status_code=r["status_code"],
                response_body=r["response_body"],
                error=r["error"],
                started_at=r["started_at"],
                completed_at=r["completed_at"],
                next_retry_at=r["next_retry_at"],
            )
            for r in rows
        ]

    def close(self) -> None:
        pass


class WebhookDispatcher:
    """Dispatch d'événements vers webhooks abonnés (async via thread pool)."""

    def __init__(self, store: WebhookStore, crypto_key_path: Path | None = None) -> None:
        self.store = store
        self.crypto_key_path = crypto_key_path
        self._lock = threading.RLock()
        self._running = False
        self._worker_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, name="webhook-worker", daemon=True)
        self._worker_thread.start()
        logger.info("Webhook dispatcher démarré")

    def stop(self) -> None:
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        logger.info("Webhook dispatcher arrêté")

    def dispatch(self, event: str, payload: dict) -> None:
        """Enfile un événement pour livraison (non bloquant)."""
        webhooks = self.store.list(active_only=True)
        matching = [w for w in webhooks if "*" in w.events or event in w.events]
        if not matching:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        import uuid

        for wh in matching:
            delivery = WebhookDelivery(
                id=uuid.uuid4().hex[:16],
                webhook_id=wh.id,
                event=event,
                payload=payload,
                attempt=0,
                status_code=None,
                response_body=None,
                error=None,
                started_at=now,
            )
            self._deliver_now(wh, delivery)

    def _deliver_now(self, wh: WebhookConfig, delivery: WebhookDelivery) -> None:
        """Tentative de livraison synchrone (appelée depuis thread worker)."""
        delivery.attempt += 1
        delivery.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Préparer la requête
        body = json.dumps(
            {
                "event": delivery.event,
                "timestamp": delivery.started_at,
                "data": delivery.payload,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        # Signature HMAC-SHA256
        signature = ""
        if wh.secret:
            try:
                from edusync_ad.core.crypto import decrypt_str, get_or_create_key

                secret = decrypt_str(get_or_create_key(self.crypto_key_path), wh.secret)
                signature = hmac.new(
                    secret.encode("utf-8"), body, hashlib.sha256
                ).hexdigest()
            except Exception:
                signature = "ERROR_DECRYPT"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "EduSync-Webhook/1.0",
            "X-Webhook-Event": delivery.event,
            "X-Webhook-Delivery": delivery.id,
            "X-Webhook-Signature": f"sha256={signature}",
        }
        headers.update(wh.headers)

        req = urllib.request.Request(wh.url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=wh.timeout_seconds) as resp:
                delivery.status_code = resp.status
                delivery.response_body = resp.read().decode("utf-8", errors="replace")[:4096]
                delivery.error = None if 200 <= resp.status < 300 else f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            delivery.status_code = exc.code
            delivery.response_body = exc.read().decode("utf-8", errors="replace")[:4096]
            delivery.error = f"HTTP {exc.code}"
        except Exception as exc:
            delivery.status_code = 0
            delivery.error = str(exc)

        delivery.completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Mise à jour last_triggered sur le webhook
        self.store.update(wh.id, last_triggered_at=delivery.completed_at, last_status=delivery.status_code, last_error=delivery.error)

        # Journaliser
        self.store.log_delivery(delivery)

        # Planifier retry si échec
        if delivery.error and delivery.attempt < wh.max_retries:
            if wh.retry_policy == "exponential":
                delay = min(2 ** delivery.attempt * 60, 3600)  # max 1h
            elif wh.retry_policy == "fixed":
                delay = 300  # 5 min
            else:
                return
            delivery.next_retry_at = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            ).isoformat(timespec="seconds")
            self.store.log_delivery(delivery)

    def _worker(self) -> None:
        """Thread de fond : retries périodiques."""
        while self._running:
            try:
                pending = self.store.get_pending_retries(limit=20)
                for delivery in pending:
                    if not self._running:
                        break
                    wh = self.store.get(delivery.webhook_id)
                    if wh and wh.active:
                        self._deliver_now(wh, delivery)
            except Exception:  # noqa: BLE001
                logger.exception("Erreur webhook worker")
            time.sleep(30)  # vérifier toutes les 30s


def load_webhooks(
    config_path: Path | None = None, deliveries_path: Path | None = None
) -> WebhookStore:
    return WebhookStore(config_path, deliveries_path)


def create_webhook_dispatcher(
    store: WebhookStore, crypto_key_path: Path | None = None
) -> WebhookDispatcher:
    return WebhookDispatcher(store, crypto_key_path)