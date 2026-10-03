"""Cache AD Local SQLite (T2) — Synchronisation en arrière-plan, invalidation TTL + uSNChanged polling.

Permet explorateur instantané, navigation fluide sur gros domaines (>5k objets).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.config import config_dir

logger = logging.getLogger("edusync_ad.ad.cache")


@dataclass
class CacheStats:
    """Statistiques du cache."""
    ous_count: int = 0
    groups_count: int = 0
    users_count: int = 0
    last_sync: float = 0.0
    last_usn_changed: int = 0
    sync_duration_ms: int = 0


class ADCacheSignals(QObject):
    """Signaux Qt pour le cache."""
    sync_started = pyqtSignal()
    sync_progress = pyqtSignal(str, int, int)  # message, current, total
    sync_finished = pyqtSignal(bool, str)  # success, message
    cache_updated = pyqtSignal()  # Notification pour rafraîchir l'UI


class _CacheSyncWorker(QThread):
    """Worker thread pour la synchronisation du cache."""
    
    def __init__(self, cache: "ADCache", full_sync: bool = False):
        super().__init__()
        self._cache = cache
        self._full_sync = full_sync
        self._cancelled = False
    
    def cancel(self) -> None:
        self._cancelled = True
    
    def run(self) -> None:
        self._cache._do_sync(self._full_sync, self._cancelled)


class ADCache:
    """Cache local SQLite pour les objets AD (OUs, groupes, utilisateurs).
    
    Synchronisation:
    - Full sync au démarrage / sur demande
    - Incremental sync via uSNChanged polling (configurable interval)
    - TTL fallback si uSNChanged non disponible
    """
    
    DEFAULT_POLL_INTERVAL_MS = 30000  # 30 secondes
    DEFAULT_TTL_SECONDS = 300  # 5 minutes
    
    def __init__(
        self,
        ad_connection: ADConnection,
        cache_path: Path | None = None,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self._ad = ad_connection
        self._cache_path = cache_path or (config_dir() / "ad_cache.sqlite")
        self._poll_interval_ms = poll_interval_ms
        self._ttl_seconds = ttl_seconds
        
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._signals = ADCacheSignals()
        self._poll_timer: QTimer | None = None
        self._sync_worker: _CacheSyncWorker | None = None
        self._stats = CacheStats()
        self._initialized = False
    
    @property
    def signals(self) -> ADCacheSignals:
        return self._signals
    
    @property
    def stats(self) -> CacheStats:
        return self._stats
    
    def initialize(self) -> None:
        """Initialise la base de données et lance la synchronisation initiale."""
        with self._lock:
            self._conn = sqlite3.connect(
                str(self._cache_path),
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-32768")  # 32MB cache
            self._create_schema()
            self._initialized = True
        
        # Sync initiale en arrière-plan
        self.sync(full=True)
        
        # Démarrer le polling périodique
        self._start_polling()
    
    def _create_schema(self) -> None:
        """Crée le schéma de la base de données."""
        assert self._conn is not None
        cursor = self._conn.cursor()
        
        # OUs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ous (
                dn TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_dn TEXT,
                depth INTEGER DEFAULT 0,
                usn_changed INTEGER DEFAULT 0,
                updated_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)
        
        # Groupes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                dn TEXT PRIMARY KEY,
                cn TEXT NOT NULL,
                ou_dn TEXT,
                group_type INTEGER,
                member_count INTEGER DEFAULT 0,
                usn_changed INTEGER DEFAULT 0,
                updated_at REAL DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (ou_dn) REFERENCES ous(dn)
            )
        """)
        
        # Utilisateurs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                dn TEXT PRIMARY KEY,
                sam TEXT NOT NULL,
                cn TEXT NOT NULL,
                given_name TEXT,
                sn TEXT,
                display_name TEXT,
                mail TEXT,
                ou_dn TEXT,
                user_account_control INTEGER DEFAULT 512,
                enabled INTEGER GENERATED ALWAYS AS ((user_account_control & 2) = 0) STORED,
                usn_changed INTEGER DEFAULT 0,
                updated_at REAL DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (ou_dn) REFERENCES ous(dn)
            )
        """)
        
        # Index pour recherches rapides
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ous_parent ON ous(parent_dn)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_groups_ou ON groups(ou_dn)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_ou ON users(ou_dn)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_sam ON users(sam)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_cn ON users(cn)")
        
        # Table de métadonnées
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        self._conn.commit()
    
    def _start_polling(self) -> None:
        """Démarre le timer de polling périodique."""
        if self._poll_timer is not None:
            return
        
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(self._on_poll_timeout)
        self._poll_timer.start()
        logger.debug("Cache polling démarré (intervalle: %dms)", self._poll_interval_ms)
    
    def _on_poll_timeout(self) -> None:
        """Appelé périodiquement pour vérifier les changements via uSNChanged."""
        if not self._initialized or self._sync_worker is not None:
            return
        self.sync(full=False)
    
    def sync(self, full: bool = False) -> None:
        """Lance une synchronisation (full ou incrémentale).
        
        Args:
            full: Si True, synchronisation complète. Sinon, incrémentale via uSNChanged.
        """
        if self._sync_worker is not None and self._sync_worker.isRunning():
            logger.debug("Sync déjà en cours, ignorée")
            return
        
        self._signals.sync_started.emit()
        self._sync_worker = _CacheSyncWorker(self, full_sync=full)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.start()
    
    def _on_sync_finished(self) -> None:
        self._sync_worker = None
    
    def _do_sync(self, full: bool, cancelled_flag: bool) -> None:
        """Exécute la synchronisation (appelé dans le worker thread)."""
        if not self._ad or self._ad.state.value != "connected":
            self._signals.sync_finished.emit(False, "Non connecté à l'AD")
            return
        
        start_time = time.time()
        base_dn = self._ad.domain_to_base_dn(self._ad.domain)
        
        try:
            if full:
                self._full_sync(base_dn, cancelled_flag)
            else:
                self._incremental_sync(base_dn, cancelled_flag)
            
            duration_ms = int((time.time() - start_time) * 1000)
            self._stats.sync_duration_ms = duration_ms
            self._stats.last_sync = time.time()
            self._save_meta("last_sync", str(self._stats.last_sync))
            self._save_meta("last_usn_changed", str(self._stats.last_usn_changed))
            
            self._signals.sync_finished.emit(True, f"Sync {'complète' if full else 'incrémentale'} en {duration_ms}ms")
            self._signals.cache_updated.emit()
            
        except Exception as exc:
            logger.exception("Erreur sync cache")
            self._signals.sync_finished.emit(False, str(exc))
    
    def _full_sync(self, base_dn: str, cancelled_flag: bool) -> None:
        """Synchronisation complète: OUs, groupes, utilisateurs."""
        logger.info("Démarrage sync complète")
        
        # 1. OUs
        self._signals.sync_progress.emit("Chargement des OUs...", 0, 3)
        ous = self._ad.list_ous(base_dn)
        self._sync_ous(ous)
        self._stats.ous_count = len(ous)
        if cancelled_flag:
            return
        
        # 2. Groupes
        self._signals.sync_progress.emit("Chargement des groupes...", 1, 3)
        groups = self._ad.list_groups(base_dn)
        self._sync_groups(groups, base_dn)
        self._stats.groups_count = len(groups)
        if cancelled_flag:
            return
        
        # 3. Utilisateurs (par OU pour progression)
        self._signals.sync_progress.emit("Chargement des utilisateurs...", 2, 3)
        users = self._ad.list_users_in_ou(base_dn)
        self._sync_users(users)
        self._stats.users_count = len(users)
        
        logger.info("Sync complète terminée: %d OUs, %d groupes, %d users", 
                   self._stats.ous_count, self._stats.groups_count, self._stats.users_count)
    
    def _incremental_sync(self, base_dn: str, cancelled_flag: bool) -> None:
        """Synchronisation incrémentale via uSNChanged (si supporté)."""
        # Pour l'instant, on fait une sync complète simplifiée
        # TODO: Implémenter vraie sync uSNChanged quand AD le supporte
        logger.debug("Sync incrémentale (fallback full sync)")
        self._full_sync(base_dn, cancelled_flag)
    
    def _sync_ous(self, ous: list[tuple[str, str]]) -> None:
        """Synchronise les OUs."""
        assert self._conn is not None
        cursor = self._conn.cursor()
        now = time.time()
        
        # Build parent map for depth calculation
        parent_map = {}
        for dn, name in ous:
            parts = dn.split(",")
            if len(parts) > 1:
                parent_map[dn] = ",".join(parts[1:])
        
        cursor.execute("DELETE FROM ous")
        for dn, name in ous:
            parent_dn = parent_map.get(dn)
            depth = 0
            if parent_dn:
                # Calculate depth by walking up
                p = parent_dn
                while p and p in parent_map:
                    depth += 1
                    p = parent_map.get(p)
            
            cursor.execute(
                "INSERT INTO ous (dn, name, parent_dn, depth, updated_at) VALUES (?, ?, ?, ?, ?)",
                (dn, name, parent_dn, depth, now),
            )
        self._conn.commit()
    
    def _sync_groups(self, groups: list[tuple[str, str]], base_dn: str) -> None:
        """Synchronise les groupes."""
        assert self._conn is not None
        cursor = self._conn.cursor()
        now = time.time()
        
        cursor.execute("DELETE FROM groups")
        for dn, cn in groups:
            # Trouver l'OU parente
            parts = dn.split(",", 1)
            ou_dn = parts[1] if len(parts) > 1 else base_dn
            
            cursor.execute(
                "INSERT INTO groups (dn, cn, ou_dn, updated_at) VALUES (?, ?, ?, ?)",
                (dn, cn, ou_dn, now),
            )
        self._conn.commit()
    
    def _sync_users(self, users: list[dict]) -> None:
        """Synchronise les utilisateurs."""
        assert self._conn is not None
        cursor = self._conn.cursor()
        now = time.time()
        
        cursor.execute("DELETE FROM users")
        for user in users:
            cursor.execute(
                """INSERT INTO users (dn, sam, cn, ou_dn, user_account_control, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["dn"], user["sam"], user["cn"], user.get("ou_dn", ""), 
                 user.get("user_account_control", 512), now),
            )
        self._conn.commit()
    
    def _save_meta(self, key: str, value: str) -> None:
        assert self._conn is not None
        cursor = self._conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self._conn.commit()
    
    # -- Requêtes cache (lecture seule, thread-safe) ---------------------------
    
    def get_ous(self, parent_dn: str | None = None) -> list[tuple[str, str]]:
        """Retourne les OUs (optionnellement filtrées par parent)."""
        with self._lock:
            if not self._conn:
                return []
            cursor = self._conn.cursor()
            if parent_dn:
                cursor.execute("SELECT dn, name FROM ous WHERE parent_dn = ? ORDER BY name", (parent_dn,))
            else:
                cursor.execute("SELECT dn, name FROM ous ORDER BY depth, name")
            return cursor.fetchall()
    
    def get_ou_children(self, ou_dn: str) -> list[dict]:
        """Retourne les enfants directs d'une OU (OUs + groupes + users)."""
        with self._lock:
            if not self._conn:
                return []
            cursor = self._conn.cursor()
            result = []
            
            # Sous-OUs
            cursor.execute("SELECT dn, name FROM ous WHERE parent_dn = ? ORDER BY name", (ou_dn,))
            for dn, name in cursor.fetchall():
                result.append({"dn": dn, "cn": name, "kind": "ou"})
            
            # Groupes
            cursor.execute("SELECT dn, cn FROM groups WHERE ou_dn = ? ORDER BY cn", (ou_dn,))
            for dn, cn in cursor.fetchall():
                result.append({"dn": dn, "cn": cn, "kind": "group"})
            
            # Utilisateurs
            cursor.execute(
                "SELECT dn, sam, cn, user_account_control FROM users WHERE ou_dn = ? ORDER BY cn",
                (ou_dn,),
            )
            for dn, sam, cn, uac in cursor.fetchall():
                result.append({
                    "dn": dn, "cn": cn, "sam": sam, "kind": "user",
                    "disabled": bool(uac & 2)
                })
            
            return result
    
    def get_groups(self, ou_dn: str | None = None) -> list[tuple[str, str]]:
        """Retourne les groupes (optionnellement filtrés par OU)."""
        with self._lock:
            if not self._conn:
                return []
            cursor = self._conn.cursor()
            if ou_dn:
                cursor.execute("SELECT dn, cn FROM groups WHERE ou_dn = ? ORDER BY cn", (ou_dn,))
            else:
                cursor.execute("SELECT dn, cn FROM groups ORDER BY cn")
            return cursor.fetchall()
    
    def get_users_in_ou(self, ou_dn: str) -> list[dict]:
        """Retourne les utilisateurs d'une OU."""
        with self._lock:
            if not self._conn:
                return []
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT dn, sam, cn, user_account_control FROM users WHERE ou_dn = ? ORDER BY cn",
                (ou_dn,),
            )
            return [
                {"dn": dn, "sam": sam, "cn": cn, "disabled": bool(uac & 2)}
                for dn, sam, cn, uac in cursor.fetchall()
            ]
    
    def search_users(self, query: str, limit: int = 100) -> list[dict]:
        """Recherche utilisateurs par SAM ou CN (LIKE)."""
        with self._lock:
            if not self._conn:
                return []
            cursor = self._conn.cursor()
            pattern = f"%{query}%"
            cursor.execute(
                """SELECT dn, sam, cn, ou_dn, user_account_control 
                   FROM users WHERE sam LIKE ? OR cn LIKE ? 
                   ORDER BY cn LIMIT ?""",
                (pattern, pattern, limit),
            )
            return [
                {"dn": dn, "sam": sam, "cn": cn, "ou_dn": ou_dn, "disabled": bool(uac & 2)}
                for dn, sam, cn, ou_dn, uac in cursor.fetchall()
            ]
    
    def search_ous(self, query: str, limit: int = 50) -> list[tuple[str, str]]:
        """Recherche OUs par nom."""
        with self._lock:
            if not self._conn:
                return []
            cursor = self._conn.cursor()
            pattern = f"%{query}%"
            cursor.execute(
                "SELECT dn, name FROM ous WHERE name LIKE ? ORDER BY depth, name LIMIT ?",
                (pattern, limit),
            )
            return cursor.fetchall()
    
    def is_stale(self) -> bool:
        """Vérifie si le cache est périmé (TTL dépassé)."""
        return (time.time() - self._stats.last_sync) > self._ttl_seconds
    
    def shutdown(self) -> None:
        """Arrête proprement le cache."""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        
        if self._sync_worker and self._sync_worker.isRunning():
            self._sync_worker.cancel()
            self._sync_worker.wait(5000)
        
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
        self._initialized = False
        logger.debug("Cache arrêté")


# Fonction utilitaire pour créer le cache depuis MainWindow
def create_ad_cache(ad_connection: ADConnection) -> ADCache:
    """Factory pour créer et initialiser le cache AD."""
    cache = ADCache(ad_connection)
    cache.initialize()
    return cache