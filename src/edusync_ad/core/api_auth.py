"""M28 — Gestion des clés API (authentification, permissions, rotation)."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from edusync_ad.core.config import config_dir

API_KEYS_FILE = config_dir() / "api_keys.json"

#: Permissions standards — peuvent être étendues par modules.
STANDARD_PERMISSIONS = {
    "*": "Accès complet (admin API)",
    "users:read": "Lister/voir les utilisateurs",
    "users:write": "Créer/modifier/supprimer des utilisateurs",
    "users:password": "Réinitialiser les mots de passe",
    "groups:read": "Lister/voir les groupes",
    "groups:write": "Créer/modifier/supprimer des groupes",
    "ous:read": "Lister/voir les OUs",
    "ous:write": "Créer/modifier/supprimer des OUs",
    "audit:read": "Consulter le journal d'audit",
    "config:read": "Lire la configuration",
    "config:write": "Modifier la configuration",
    "portal:read": "Consulter le portail auto-service",
    "portal:write": "Gérer le portail (démarrer/arrêter, valider demandes)",
    "webhooks:read": "Lister les webhooks",
    "webhooks:write": "Créer/modifier/supprimer des webhooks",
}

PERMISSION_LABELS = {k: v for k, v in STANDARD_PERMISSIONS.items()}


@dataclass
class APIKeyInfo:
    """Métadonnées d'une clé API (stockées en clair, la clé elle-même est hachée)."""

    id: str  # identifiant court (ex. "ak_abc123")
    name: str  # nom lisible
    key_hash: str  # SHA-256 de la clé complète
    prefix: str  # premiers 8 caractères pour affichage (ex. "edusync_abcd")
    permissions: list[str]  # liste de permissions
    created_at: str  # ISO 8601
    expires_at: str | None = None  # ISO 8601 ou null = jamais
    last_used_at: str | None = None
    active: bool = True

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            return datetime.fromisoformat(self.expires_at.replace("Z", "+00:00")) <= datetime.now(
                timezone.utc
            )
        except (ValueError, TypeError):
            return False


class APIKeyStore:
    """Stockage et validation des clés API (thread-safe)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else API_KEYS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._keys: dict[str, APIKeyInfo] = {}  # id -> info
        self._prefix_index: dict[str, str] = {}  # prefix -> id
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        info = APIKeyInfo(**item)
                        self._keys[info.id] = info
                        self._prefix_index[info.prefix] = info.id
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        with self._lock:
            data = [asdict(k) for k in self._keys.values()]
            with self.path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

    @staticmethod
    def _hash_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _generate_key() -> tuple[str, str]:
        """Retourne (clé_complete, prefix_8_chars)."""
        # Format : edusync_<32_chars> = 42 chars total, prefix = edusync_abcd (12 chars)
        raw = secrets.token_urlsafe(24)  # ~32 chars
        full = f"edusync_{raw}"
        return full, full[:12]

    def create(
        self,
        name: str,
        permissions: list[str],
        *,
        expires_days: int | None = None,
    ) -> tuple[str, APIKeyInfo]:
        """Crée une nouvelle clé. Retourne (clé_complete, info). La clé complète n'est JAMAIS stockée."""
        if not name.strip():
            raise ValueError("Nom requis")
        valid_perms = [p for p in permissions if p in STANDARD_PERMISSIONS]
        if not valid_perms:
            raise ValueError("Aucune permission valide")

        full_key, prefix = self._generate_key()
        key_hash = self._hash_key(full_key)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        expires = (
            (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(timespec="seconds")
            if expires_days
            else None
        )
        key_id = f"ak_{secrets.token_hex(6)}"

        info = APIKeyInfo(
            id=key_id,
            name=name.strip(),
            key_hash=key_hash,
            prefix=prefix,
            permissions=valid_perms,
            created_at=now,
            expires_at=expires,
            active=True,
        )
        with self._lock:
            self._keys[key_id] = info
            self._prefix_index[prefix] = key_id
            self._save()
        return full_key, info

    def validate(self, key: str) -> Optional[APIKeyInfo]:
        """Valide une clé complète. Retourne l'info si valide, None sinon."""
        if not key or not key.startswith("edusync_"):
            return None
        prefix = key[:12]
        with self._lock:
            key_id = self._prefix_index.get(prefix)
            if not key_id:
                return None
            info = self._keys.get(key_id)
            if not info or not info.active or info.is_expired:
                return None
            if info.key_hash != self._hash_key(key):
                return None
            # Mise à jour last_used_at
            info.last_used_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save()
            return info

    def list_keys(self, include_inactive: bool = False) -> list[APIKeyInfo]:
        with self._lock:
            keys = list(self._keys.values())
        if not include_inactive:
            keys = [k for k in keys if k.active and not k.is_expired]
        keys.sort(key=lambda k: k.created_at, reverse=True)
        return keys

    def get_key(self, key_id: str) -> Optional[APIKeyInfo]:
        with self._lock:
            return self._keys.get(key_id)

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            info = self._keys.get(key_id)
            if not info:
                return False
            info.active = False
            self._prefix_index.pop(info.prefix, None)
            self._save()
            return True

    def delete(self, key_id: str) -> bool:
        with self._lock:
            info = self._keys.pop(key_id, None)
            if info:
                self._prefix_index.pop(info.prefix, None)
                self._save()
                return True
            return False

    def update_permissions(self, key_id: str, permissions: list[str]) -> Optional[APIKeyInfo]:
        valid_perms = [p for p in permissions if p in STANDARD_PERMISSIONS]
        with self._lock:
            info = self._keys.get(key_id)
            if not info:
                return None
            info.permissions = valid_perms
            self._save()
            return info

    def close(self) -> None:
        pass  # pas de connexion persistante


def load_api_keys(path: Path | None = None) -> APIKeyStore:
    return APIKeyStore(path)


def save_api_keys(store: APIKeyStore, path: Path | None = None) -> Path:
    store._save()  # type: ignore
    return store.path
