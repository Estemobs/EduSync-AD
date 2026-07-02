"""Chiffrement AES-256 des identifiants de connexion (§2, §3, §12).

Seuls le domaine et le nom d'utilisateur sont éventuellement persistés
(option "Mémoriser la connexion"). Le mot de passe n'est **jamais** écrit
sur disque, ici ou ailleurs dans l'application.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from edusync_ad.core.config import config_dir

KEY_SIZE_BYTES = 32  # AES-256
NONCE_SIZE_BYTES = 12


def _key_file_path() -> Path:
    return config_dir() / "secret.key"


def get_or_create_key(path: Path | None = None) -> bytes:
    path = path or _key_file_path()
    if path.exists():
        return path.read_bytes()
    key = AESGCM.generate_key(bit_length=256)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def encrypt_str(key: bytes, plaintext: str) -> str:
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_str(key: bytes, token: str) -> str:
    raw = base64.b64decode(token.encode("ascii"))
    nonce, ciphertext = raw[:NONCE_SIZE_BYTES], raw[NONCE_SIZE_BYTES:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


@dataclass
class RememberedConnection:
    domaine: str
    controleur: str
    utilisateur: str
    # Optionnel : uniquement si l'utilisateur a explicitement coché
    # "Mémoriser le mot de passe" (opt-in distinct, désactivé par défaut).
    mot_de_passe: str | None = None


def _remembered_connection_path() -> Path:
    return config_dir() / "connection.local.json"


def save_remembered_connection(
    info: RememberedConnection, *, key_path: Path | None = None, path: Path | None = None
) -> None:
    key = get_or_create_key(key_path)
    path = path or _remembered_connection_path()
    payload = {
        field: encrypt_str(key, value)
        for field, value in asdict(info).items()
        if value is not None
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Permissions restrictives : ce fichier peut désormais contenir un mot de
    # passe chiffré, au même titre que secret.key.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.write(fd, json.dumps(payload).encode("utf-8"))
    finally:
        os.close(fd)


def load_remembered_connection(
    *, key_path: Path | None = None, path: Path | None = None
) -> RememberedConnection | None:
    path = path or _remembered_connection_path()
    if not path.exists():
        return None
    key = get_or_create_key(key_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        decrypted = {field: decrypt_str(key, token) for field, token in payload.items()}
        return RememberedConnection(**decrypted)
    except Exception:
        return None


def clear_remembered_connection(path: Path | None = None) -> None:
    path = path or _remembered_connection_path()
    path.unlink(missing_ok=True)
