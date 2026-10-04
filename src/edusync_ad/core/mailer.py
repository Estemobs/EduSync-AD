"""Envoi de courriels SMTP — mutualisé par les étiquettes (M24) et le portail
auto-service (M27).

Le mot de passe SMTP n'est chiffré qu'au stockage (AES-256, ``core/crypto.py``),
jamais écrit en clair. Aucune dépendance externe : ``smtplib`` de la
bibliothèque standard uniquement.
"""

from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from edusync_ad.core.config import config_dir
from edusync_ad.core.crypto import decrypt_str, encrypt_str, get_or_create_key

#: Nom de fichier conservé depuis M24 (l'UI des étiquettes l'utilise).
MAIL_CONFIG_FILE = config_dir() / "label_mail.json"


class MailError(Exception):
    """Échec de configuration ou d'envoi SMTP (message déjà en français)."""


@dataclass
class MailConfig:
    """Configuration SMTP — le mot de passe n'est chiffré qu'au stockage."""

    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    use_ssl: bool = False
    from_addr: str = ""
    from_name: str = "EduSync AD"
    timeout: float = 20.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (self.host or "").strip():
            errors.append("Serveur SMTP renseigné requis.")
        if not (1 <= int(self.port) <= 65535):
            errors.append("Port SMTP invalide (1-65535).")
        if not (self.from_addr or "").strip():
            errors.append("Adresse d'expéditrice requise.")
        elif "@" not in self.from_addr:
            errors.append(f"Adresse d'expéditrice « {self.from_addr} » invalide.")
        if self.use_ssl and self.use_tls:
            errors.append("SSL implicite et STARTTLS sont exclusifs.")
        return errors

    def to_stored_dict(self, key_path: Path | None = None) -> dict:
        data = {
            "host": self.host,
            "port": int(self.port),
            "username": self.username,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "from_addr": self.from_addr,
            "from_name": self.from_name,
            "timeout": self.timeout,
        }
        if self.password:
            data["password_token"] = encrypt_str(get_or_create_key(key_path), self.password)
        return data

    @classmethod
    def from_stored_dict(cls, data: dict, key_path: Path | None = None) -> "MailConfig":
        config = cls(
            host=str(data.get("host", "")),
            port=int(data.get("port", 587)),
            username=str(data.get("username", "")),
            use_tls=bool(data.get("use_tls", True)),
            use_ssl=bool(data.get("use_ssl", False)),
            from_addr=str(data.get("from_addr", "")),
            from_name=str(data.get("from_name", "EduSync AD")),
            timeout=float(data.get("timeout", 20.0)),
        )
        token = data.get("password_token")
        if token:
            try:
                config.password = decrypt_str(get_or_create_key(key_path), str(token))
            except Exception:
                config.password = ""  # clé de chiffrement perdue : à ressaisir
        return config


def load_mail_config(path: Path | None = None, key_path: Path | None = None) -> MailConfig:
    dest = path if path is not None else MAIL_CONFIG_FILE
    if dest.exists():
        try:
            with dest.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return MailConfig.from_stored_dict(data, key_path)
        except (OSError, ValueError, TypeError):
            pass
    return MailConfig()


def save_mail_config(
    config: MailConfig, path: Path | None = None, key_path: Path | None = None
) -> Path:
    errors = config.validate()
    if errors:
        raise MailError("Configuration SMTP invalide : " + "; ".join(errors))
    dest = path if path is not None else MAIL_CONFIG_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(config.to_stored_dict(key_path), fh, indent=2, ensure_ascii=False)
    return dest


def build_message(
    config: MailConfig, to_addr: str, subject: str, body: str, *, default_subject: str = ""
) -> EmailMessage:
    """Construit le message (configuration et destinataire contrôlés).

    Lève :class:`MailError` si la configuration SMTP est incomplète ou si le
    destinataire n'est pas une adresse valide.
    """
    errors = config.validate()
    if errors:
        raise MailError("Configuration SMTP invalide : " + "; ".join(errors))
    to_addr = (to_addr or "").strip()
    if "@" not in to_addr:
        raise MailError(f"Adresse du destinataire « {to_addr} » invalide.")

    message = EmailMessage()
    message["Subject"] = subject.strip() or default_subject or "Message EduSync AD"
    message["From"] = f"{config.from_name} <{config.from_addr}>"
    message["To"] = to_addr
    message.set_content(body or "")
    return message


def send_message(config: MailConfig, message: EmailMessage, *, smtp_factory=None) -> None:
    """Transport SMTP d'un message déjà construit — lève :class:`MailError`.

    ``smtp_factory(hôte, port, timeout)`` → objet compatible ``smtplib.SMTP``
    (injectable pour les tests).
    """

    def _connect(host: str, port: int, timeout: float):
        if config.use_ssl:
            return smtplib.SMTP_SSL(host, port, timeout=timeout)
        return smtplib.SMTP(host, port, timeout=timeout)

    factory = smtp_factory or _connect
    try:
        with factory(config.host, int(config.port), config.timeout) as server:
            if not config.use_ssl and config.use_tls:
                server.starttls()
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message)
    except MailError:
        raise
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(f"Authentification SMTP refusée ({getattr(exc, 'smtp_code', '?')}).") from exc
    except smtplib.SMTPRecipientsRefused:
        raise MailError("Destinataire refusé par le serveur SMTP.") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Envoi impossible : {exc}") from exc


def send_mail(
    config: MailConfig,
    to_addr: str,
    subject: str,
    body: str,
    *,
    default_subject: str = "",
    smtp_factory=None,
) -> None:
    """Envoie un courriel sans pièce jointe — lève :class:`MailError`."""
    message = build_message(config, to_addr, subject, body, default_subject=default_subject)
    send_message(config, message, smtp_factory=smtp_factory)
