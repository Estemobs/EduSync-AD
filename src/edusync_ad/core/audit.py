"""Journal d'actions (§11 du cahier des charges).

Chaque opération est enregistrée localement (horodatage, type d'action,
compte concerné, OU source/destination, résultat, identifiant de session,
indicateur simulation). Stocké uniquement sur la machine de l'administrateur
(SQLite), consultable avec filtres et exportable en CSV.
"""

from __future__ import annotations

import csv
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_data_dir

from edusync_ad.core.models import ActionLogEntry

APP_NAME = "EduSyncAD"
APP_AUTHOR = "EduSyncAD"


def data_dir() -> Path:
    path = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    return data_dir() / "journal.db"


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        # Compte admin de la session courante — renseigné après connexion AD
        # (voir MainWindow), inclus automatiquement dans chaque entrée sans
        # devoir modifier tous les appels à record().
        self.current_user: str = ""

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action_type TEXT NOT NULL,
                compte TEXT NOT NULL,
                ou_source TEXT,
                ou_destination TEXT,
                resultat TEXT NOT NULL,
                session_id TEXT NOT NULL,
                simulation INTEGER NOT NULL,
                detail TEXT
            )
            """
        )
        existing_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(actions)")}
        if "utilisateur" not in existing_cols:
            self._conn.execute("ALTER TABLE actions ADD COLUMN utilisateur TEXT")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_dn TEXT NOT NULL UNIQUE,
                sam_account_name TEXT NOT NULL,
                nom_complet TEXT,
                moved_at TEXT NOT NULL,
                session_id TEXT NOT NULL
            )
            """
        )
        pending_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(pending_deletions)")}
        if "delai_jours" not in pending_cols:
            self._conn.execute("ALTER TABLE pending_deletions ADD COLUMN delai_jours INTEGER")
        self._conn.commit()

    def log(self, entry: ActionLogEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO actions
                (timestamp, action_type, compte, ou_source, ou_destination,
                 resultat, session_id, simulation, detail, utilisateur)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.timestamp,
                entry.action_type,
                entry.compte,
                entry.ou_source,
                entry.ou_destination,
                entry.resultat,
                entry.session_id,
                int(entry.simulation),
                entry.detail,
                entry.utilisateur,
            ),
        )
        self._conn.commit()

    def record(
        self,
        action_type: str,
        compte: str,
        resultat: str,
        session_id: str,
        *,
        ou_source: str | None = None,
        ou_destination: str | None = None,
        simulation: bool = False,
        detail: str = "",
    ) -> ActionLogEntry:
        entry = ActionLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            action_type=action_type,
            compte=compte,
            ou_source=ou_source,
            ou_destination=ou_destination,
            resultat=resultat,
            session_id=session_id,
            simulation=simulation,
            detail=detail,
            utilisateur=self.current_user,
        )
        self.log(entry)
        return entry

    def query(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        action_type: str | None = None,
        resultat: str | None = None,
    ) -> list[ActionLogEntry]:
        clauses: list[str] = []
        params: list[str] = []
        if date_from:
            clauses.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("timestamp <= ?")
            params.append(date_to)
        if action_type:
            clauses.append("action_type = ?")
            params.append(action_type)
        if resultat:
            clauses.append("resultat = ?")
            params.append(resultat)

        sql = "SELECT * FROM actions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> ActionLogEntry:
        return ActionLogEntry(
            timestamp=row["timestamp"],
            action_type=row["action_type"],
            compte=row["compte"],
            ou_source=row["ou_source"],
            ou_destination=row["ou_destination"],
            resultat=row["resultat"],
            session_id=row["session_id"],
            simulation=bool(row["simulation"]),
            detail=row["detail"] or "",
            utilisateur=row["utilisateur"] or "",
        )

    def export_csv(self, path: Path) -> None:
        entries = self.query()
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(
                [
                    "timestamp",
                    "action_type",
                    "compte",
                    "ou_source",
                    "ou_destination",
                    "resultat",
                    "session_id",
                    "simulation",
                    "detail",
                    "utilisateur",
                ]
            )
            for entry in entries:
                writer.writerow(
                    [
                        entry.timestamp,
                        entry.action_type,
                        entry.compte,
                        entry.ou_source or "",
                        entry.ou_destination or "",
                        entry.resultat,
                        entry.session_id,
                        "oui" if entry.simulation else "non",
                        entry.detail,
                        entry.utilisateur,
                    ]
                )

    # -- File d'attente de suppressions différées ----------------------------

    def add_pending_deletion(
        self, user_dn: str, sam_account_name: str, nom_complet: str, session_id: str,
        delai_jours: int,
    ) -> None:
        """`delai_jours` est figé au moment de l'archivage — modifier le délai
        par défaut dans les Paramètres n'affecte pas les suppressions déjà
        programmées, seulement les nouvelles."""
        moved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.execute(
            """
            INSERT OR REPLACE INTO pending_deletions
                (user_dn, sam_account_name, nom_complet, moved_at, session_id, delai_jours)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_dn, sam_account_name, nom_complet, moved_at, session_id, delai_jours),
        )
        self._conn.commit()

    def get_pending_deletions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM pending_deletions ORDER BY moved_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def count_due_deletions(self, default_delai_jours: int) -> int:
        return len(self.get_due_deletions(default_delai_jours))

    def get_due_deletions(self, default_delai_jours: int) -> list[dict]:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        due = []
        for row in self.get_pending_deletions():
            delai = row.get("delai_jours")
            if delai is None:
                delai = default_delai_jours
            try:
                moved_at = datetime.fromisoformat(row["moved_at"])
            except ValueError:
                continue
            if moved_at + timedelta(days=delai) <= now:
                due.append(row)
        return due

    def remove_pending_deletion(self, user_dn: str) -> None:
        self._conn.execute("DELETE FROM pending_deletions WHERE user_dn = ?", (user_dn,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
