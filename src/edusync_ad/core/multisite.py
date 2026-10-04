"""Multisite / multi-domaine (M25 du cahier des charges).

Un même EduSync AD peut piloter plusieurs annuaires (plusieurs
établissements, domaines ou forêts). Les profils de domaine — libellé, nom
de domaine, contrôleur, identifiant de connexion et, sur demande explicite,
le mot de passe associé — sont regroupés dans ``domaines.json``.

Ce fichier est **chiffré** avec la clé AES-256 locale (``secret.key``), au
même titre que ``connection.local.json``, et écrit avec des permissions
0600 : il peut contenir des identifiants. Il reste local à la machine de
l'administrateur et n'est jamais transmis nulle part.

Un seul domaine est connecté à la fois : le changement de site ferme la
session en cours (voir ``app.py``) et rouvre l'écran de connexion prérempli
avec le profil choisi.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Iterable

from edusync_ad.core.config import config_dir
from edusync_ad.core.crypto import (
    decrypt_str,
    encrypt_str,
    get_or_create_key,
    load_remembered_connection,
)

logger = logging.getLogger("edusync_ad.multisite")

SITES_FORMAT_VERSION = 1

# Nom de domaine DNS (RFC 1123 simplifié) : labels alphanumériques et tirets,
# séparés par des points. Un ID unique (sid) AD serait accepté aussi mais le
# nom de domaine est ce que saisit l'administrateur dans l'écran de connexion.
_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$"
)


class MultisiteError(Exception):
    """Fichier ``domaines.json`` illisible (corrompu, clé perdue, format inconnu)."""


@dataclass
class DomainProfile:
    """Un domaine/forêt géré par l'application."""

    id: str
    label: str
    domain: str
    controller: str = ""
    username: str = ""
    # Mot de passe de l'administrateur : jamais écrit en clair, uniquement
    # si `remember_password` est coché (opt-in, comme en écran de connexion).
    password: str = ""
    remember_password: bool = False
    verify_certificate: bool = True
    ca_cert_path: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.label} — {self.domain}" if self.label else self.domain

    @property
    def stored_password(self) -> str:
        """Mot de passe conservé, ou vide si l'utilisateur ne l'a pas mémorisé."""
        return self.password if self.remember_password else ""

    def validate(self) -> list[str]:
        """Messages d'erreur (vide = profil valide)."""
        errors: list[str] = []
        if not self.label.strip():
            errors.append("Le libellé du site est obligatoire.")
        if not self.domain.strip():
            errors.append("Le nom de domaine est obligatoire.")
        elif not _DOMAIN_RE.match(self.domain.strip()):
            errors.append(f"Nom de domaine invalide : « {self.domain} ».")
        if self.remember_password and not self.password:
            errors.append("Le mot de passe à mémoriser est vide.")
        return errors

    def sanitized(self) -> DomainProfile:
        """Copie prête à être écrite sur disque (mot de passe abandonné si non mémorisé)."""
        return DomainProfile(
            id=self.id,
            label=self.label.strip(),
            domain=self.domain.strip().lower(),
            controller=self.controller.strip(),
            username=self.username.strip(),
            password=self.stored_password,
            remember_password=self.remember_password,
            verify_certificate=self.verify_certificate,
            ca_cert_path=self.ca_cert_path.strip(),
        )


def sites_path() -> Path:
    return config_dir() / "domaines.json"


def unique_site_id(existing: Iterable[DomainProfile] = ()) -> str:
    taken = {profile.id for profile in existing}
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in taken:
            return candidate


def new_profile(
    label: str,
    domain: str,
    *,
    controller: str = "",
    username: str = "",
    password: str = "",
    remember_password: bool = False,
    verify_certificate: bool = True,
    ca_cert_path: str = "",
    existing: Iterable[DomainProfile] = (),
) -> DomainProfile:
    return DomainProfile(
        id=unique_site_id(existing),
        label=label,
        domain=domain,
        controller=controller,
        username=username,
        password=password,
        remember_password=remember_password,
        verify_certificate=verify_certificate,
        ca_cert_path=ca_cert_path,
    )


def _profiles_from_payload(payload: object) -> list[DomainProfile]:
    if not isinstance(payload, list):
        raise MultisiteError("Structure inattendue dans domaines.json")
    known = {field.name for field in fields(DomainProfile)}
    profiles: list[DomainProfile] = []
    for item in payload:
        if not isinstance(item, dict):
            raise MultisiteError("Profil de domaine illisible dans domaines.json")
        values = {key: value for key, value in item.items() if key in known}
        profile = DomainProfile(**values)  # type: ignore[arg-type]
        if not profile.id:
            profile.id = unique_site_id(profiles)
        profiles.append(profile)
    return profiles


def load_sites(path: Path | None = None, *, key_path: Path | None = None) -> list[DomainProfile]:
    """Charge ``domaines.json``. Lève :class:`MultisiteError` si illisible."""
    path = path or sites_path()
    if not path.exists():
        return []
    key = get_or_create_key(key_path)
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        token = envelope["payload"]
        version = envelope.get("version", SITES_FORMAT_VERSION)
        if version != SITES_FORMAT_VERSION:
            raise MultisiteError(f"Version de fichier de domaines non gérée : {version}")
        return _profiles_from_payload(json.loads(decrypt_str(key, token)))
    except MultisiteError:
        raise
    except Exception as exc:  # clé perdue, JSON cassé, fichier tronqué…
        raise MultisiteError(f"domaines.json illisible : {exc}") from exc


def save_sites(
    profiles: Iterable[DomainProfile], path: Path | None = None, *, key_path: Path | None = None
) -> None:
    """Écrit ``domaines.json`` chiffré, permissions 0600."""
    key = get_or_create_key(key_path)
    path = path or sites_path()
    cleaned = [profile.sanitized() for profile in profiles]
    blob = json.dumps([asdict(profile) for profile in cleaned], ensure_ascii=False)
    envelope = {
        "version": SITES_FORMAT_VERSION,
        "payload": encrypt_str(key, blob),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.write(fd, json.dumps(envelope).encode("utf-8"))
    finally:
        os.close(fd)


def find_profile(profiles: Iterable[DomainProfile], site_id: str | None) -> DomainProfile | None:
    if not site_id:
        return None
    for profile in profiles:
        if profile.id == site_id:
            return profile
    return None


def profile_for_domain(profiles: Iterable[DomainProfile], domain: str | None) -> DomainProfile | None:
    if not domain:
        return None
    wanted = domain.strip().lower()
    for profile in profiles:
        if profile.domain.strip().lower() == wanted:
            return profile
    return None


def import_legacy_connection(
    path: Path | None = None,
    *,
    key_path: Path | None = None,
    legacy_path: Path | None = None,
) -> DomainProfile | None:
    """Migre ``connection.local.json`` (M9 « mémoriser la connexion »).

    Retourne ``None`` s'il n'y a rien à migrer. Le mot de passe n'est repris
    que si l'administrateur avait coché « Mémoriser aussi le mot de passe ».
    """
    remembered = load_remembered_connection(key_path=key_path, path=legacy_path)
    if remembered is None or not remembered.domaine:
        return None
    existing = load_sites(path, key_path=key_path)
    if profile_for_domain(existing, remembered.domaine) is not None:
        return None
    return new_profile(
        label=remembered.domaine,
        domain=remembered.domaine,
        controller=remembered.controleur,
        username=remembered.utilisateur,
        password=remembered.mot_de_passe or "",
        remember_password=remembered.mot_de_passe is not None,
        existing=existing,
    )


def ensure_sites(
    path: Path | None = None, *, key_path: Path | None = None, legacy_path: Path | None = None
) -> list[DomainProfile]:
    """Charge les sites, avec migration de la connexion mémorisée si le fichier est vide.

    Ne lève jamais : un fichier illisible est simplement ignoré (et signalé
    dans les logs) pour ne pas bloquer le démarrage de l'application.
    """
    try:
        profiles = load_sites(path, key_path=key_path)
        if profiles:
            return profiles
        legacy = import_legacy_connection(path, key_path=key_path, legacy_path=legacy_path)
    except MultisiteError as exc:
        logger.warning("Fichier de domaines ignoré : %s", exc)
        return []
    if legacy is None:
        return []
    try:
        save_sites([legacy], path, key_path=key_path)
    except OSError as exc:
        logger.warning("Impossible d'enregistrer le site migré : %s", exc)
    return [legacy]
