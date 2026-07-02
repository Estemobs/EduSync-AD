"""Point d'entrée applicatif."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import load_config
from edusync_ad.ui.login_dialog import LoginDialog
from edusync_ad.ui.main_window import MainWindow
from edusync_ad.ui.theme import stylesheet_for


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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EduSync AD")
    app.setWindowIcon(_app_icon())

    config = load_config()
    app.setStyleSheet(stylesheet_for(config.theme))

    login = LoginDialog()
    if login.exec() != LoginDialog.DialogCode.Accepted:
        return 0

    audit_log = AuditLog()
    window = MainWindow(login.ad_connection, config, audit_log)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
