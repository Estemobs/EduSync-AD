"""Point d'entrée applicatif."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import load_config
from edusync_ad.core.multisite import DomainProfile, ensure_sites, find_profile
from edusync_ad.ui.login_dialog import LoginDialog
from edusync_ad.ui.main_window import MainWindow
from edusync_ad.ui.theme import stylesheet_for

logger = logging.getLogger("edusync_ad.app")


def _app_icon() -> QIcon:
    """Résout l'icône : thème système (Flatpak) puis fichier embarqué (exe/dev)."""
    icon = QIcon.fromTheme("org.edusync.AD")
    if not icon.isNull():
        return icon

    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parents[2]

    for name in ("icon.ico", "icon.png"):
        candidate = base / "assets" / name
        if candidate.exists():
            return QIcon(str(candidate))

    return QIcon()


def _record_site_switch(audit_log: AuditLog, site_id: str, session_id: str) -> DomainProfile | None:
    """Journalise le changement de site dans l'ancien domaine, puis renvoie le profil cible."""
    profile = find_profile(ensure_sites(), site_id)
    if profile is None:
        logger.warning("Profil de domaine introuvable : %s", site_id)
        return None
    audit_log.record(
        "changement_domaine",
        audit_log.current_user,
        "succes",
        session_id,
        detail=f"{audit_log.current_domain or '—'} → {profile.domain}",
    )
    return profile


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EduSync AD")
    app.setWindowIcon(_app_icon())

    config = load_config()
    app.setStyleSheet(stylesheet_for(config.theme))

    # Un seul domaine connecté à la fois (M25) : boucle de session. À chaque
    # tour, l'écran de connexion s'ouvre (prérempli avec le site demandé) puis
    # la fenêtre principale se ferme à la fin de la session.
    audit_log = AuditLog()
    pending: DomainProfile | None = None

    while True:
        login = LoginDialog(config=config, profile=pending)
        pending = None
        if login.exec() != LoginDialog.DialogCode.Accepted:
            return 0

        audit_log.current_user = login.ad_connection.username or ""
        audit_log.current_domain = login.ad_connection.domain or ""

        window = MainWindow(login.ad_connection, config, audit_log)
        requested: dict[str, str | None] = {"site": None}

        def _on_site_switched(site_id: str) -> None:
            requested["site"] = site_id
            window.close()  # app.exec() rend la main → nouvelle session

        window.site_switched.connect(_on_site_switched)
        window.show()

        code = app.exec()

        site_id = requested["site"]
        if not site_id:
            return code

        # Le changement de site a été confirmé : on referme l'ancienne session,
        # on journalise la bascule et on repart sur l'écran de connexion.
        pending = _record_site_switch(audit_log, site_id, window.session_id)
        login.ad_connection.disconnect()
        if pending is None:
            return code


if __name__ == "__main__":
    sys.exit(main())
