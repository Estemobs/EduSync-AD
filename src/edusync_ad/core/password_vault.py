"""Coffre-fort local des mots de passe positionnés par l'application.

Exception délibérée et strictement limitée à la règle énoncée dans
core/crypto.py ("le mot de passe n'est jamais écrit sur disque") : celle-ci
concerne les identifiants de *connexion* à l'AD. Ici, c'est différent — sur
demande explicite de l'utilisateur, EduSync AD retient les mots de passe
*de comptes élèves/personnels* qu'il vient lui-même de positionner (création,
réinitialisation), pour pouvoir les réafficher ensuite (fiche utilisateur,
export CSV/étiquettes) sans repasser par une réinitialisation. Un mot de
passe changé par un autre outil (ADUC, PowerShell...) n'est jamais connu
d'EduSync AD et reste donc introuvable ici — c'est le comportement attendu,
pas un bug.

Chiffrement AES-256-GCM avec la même clé que core/crypto.py (fichier
`secret.key`, permissions restrictives). Stocké dans une base SQLite locale,
elle aussi à permissions restrictives — jamais sur le serveur AD.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from edusync_ad.core.audit import data_dir
from edusync_ad.core.crypto import decrypt_str, encrypt_str, get_or_create_key


def default_vault_path() -> Path:
    return data_dir() / "password_vault.sqlite3"


class PasswordVault:
    def __init__(self, path: Path | None = None, *, key_path: Path | None = None) -> None:
        self.path = path or default_vault_path()
        self._key_path = key_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mots_de_passe (
                sam TEXT PRIMARY KEY,
                secret TEXT NOT NULL,
                horodatage TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _key(self) -> bytes:
        return get_or_create_key(self._key_path)

    def store(self, sam: str, password: str) -> None:
        if not sam or not password:
            return
        token = encrypt_str(self._key(), password)
        self._conn.execute(
            """
            INSERT INTO mots_de_passe (sam, secret, horodatage) VALUES (?, ?, datetime('now'))
            ON CONFLICT(sam) DO UPDATE SET secret = excluded.secret, horodatage = excluded.horodatage
            """,
            (sam.lower(), token),
        )
        self._conn.commit()

    def get(self, sam: str) -> str | None:
        if not sam:
            return None
        row = self._conn.execute(
            "SELECT secret FROM mots_de_passe WHERE sam = ?", (sam.lower(),)
        ).fetchone()
        if row is None:
            return None
        try:
            return decrypt_str(self._key(), row[0])
        except Exception:
            # Clé absente/changée ou entrée corrompue : traité comme "non
            # enregistré" plutôt que de faire planter l'appelant.
            return None

    def has(self, sam: str) -> bool:
        if not sam:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM mots_de_passe WHERE sam = ?", (sam.lower(),)
        ).fetchone()
        return row is not None

    def delete(self, sam: str) -> None:
        if not sam:
            return
        self._conn.execute("DELETE FROM mots_de_passe WHERE sam = ?", (sam.lower(),))
        self._conn.commit()

    def clear_all(self) -> int:
        """Purge tout le coffre — utilisé par le bouton Paramètres. Retourne
        le nombre d'entrées supprimées."""
        count = self._conn.execute("SELECT COUNT(*) FROM mots_de_passe").fetchone()[0]
        self._conn.execute("DELETE FROM mots_de_passe")
        self._conn.commit()
        return count

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM mots_de_passe").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
