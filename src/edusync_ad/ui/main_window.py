"""Fenêtre principale : bandeau (connexion, mises à jour, rapport de bug), sidebar, pages."""

from __future__ import annotations

import platform
import webbrowser
from urllib.parse import quote

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.audit import AuditLog, new_session_id
from edusync_ad.core.config import AppConfig, save_config
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.core.updater import CURRENT_VERSION, check_for_update
from edusync_ad.ui.audit_page import AuditPage
from edusync_ad.ui.log_manager import AppLogManager
from edusync_ad.ui.log_view_widget import LogViewWidget
from edusync_ad.ui.modules.ad_explorer_page import ADExplorerPage
from edusync_ad.ui.modules.create_accounts_page import CreateAccountsPage
from edusync_ad.ui.modules.depart_page import DepartPage
from edusync_ad.ui.modules.export_page import ExportPage
from edusync_ad.ui.modules.migration_page import MigrationPage
from edusync_ad.ui.modules.password_reset_page import PasswordResetPage
from edusync_ad.ui.settings_page import SettingsPage
from edusync_ad.ui.theme import status_colors_for, stylesheet_for
from edusync_ad.ui.update_dialog import UpdateDialog

ISSUE_TRACKER_URL = "https://github.com/estemobs/EduSync-AD/issues/new"
MAX_LOG_EXCERPT_CHARS = 3000


def _platform_suffix() -> str:
    system = platform.system()
    if system == "Windows":
        return "-win"
    if system == "Linux":
        return "-lin"
    return ""


class _StartupUpdateCheckWorker(QThread):
    found = pyqtSignal(object)

    def run(self) -> None:
        self.found.emit(check_for_update())


class MainWindow(QMainWindow):
    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.password_vault = PasswordVault()
        self.session_id = new_session_id()

        self.setWindowTitle(f"EduSync AD — v{CURRENT_VERSION}{_platform_suffix()}")
        self.resize(1100, 720)

        self._build_top_bar()
        self._build_body()
        self.apply_theme()

        self._update_check_worker: _StartupUpdateCheckWorker | None = None
        QTimer.singleShot(1500, self._check_update_on_startup)

    def _check_update_on_startup(self) -> None:
        self._update_check_worker = _StartupUpdateCheckWorker()
        self._update_check_worker.found.connect(self._on_startup_update_found)
        self._update_check_worker.start()

    def _on_startup_update_found(self, info: dict | None) -> None:
        if info is None:
            return
        dlg = UpdateDialog(self, initial_info=info)
        dlg.show()

    def _build_top_bar(self) -> None:
        top_bar = QWidget()
        top_bar.setObjectName("TopBar")
        layout = QHBoxLayout(top_bar)

        self.connection_label = QLabel()
        self._connection_state = "connected"
        self._connection_domain = self.ad_connection.domain or ""
        self._connection_protocol = "LDAPS" if self.ad_connection.used_ldaps else "LDAP (non chiffré)"
        self._refresh_connection_label()
        # L'indicateur peut être mis à jour depuis l'extérieur via set_connection_state()
        layout.addWidget(self.connection_label)
        layout.addStretch()

        report_btn = QPushButton("🐞 Signaler un problème")
        report_btn.clicked.connect(self._on_report_issue)
        layout.addWidget(report_btn)

        update_btn = QPushButton("⟳ Mises à jour")
        update_btn.clicked.connect(self._on_check_update)
        layout.addWidget(update_btn)

        version_label = QLabel(f"v{CURRENT_VERSION}{_platform_suffix()}")
        version_label.setStyleSheet("color: #888; font-size: 11px; padding-left: 6px;")
        layout.addWidget(version_label)

        self.setMenuWidget(top_bar)

    def set_connection_state(self, state: str, domain: str = "", protocol: str = "") -> None:
        """Met à jour l'indicateur tricolore. state : 'connected' | 'connecting' | 'disconnected'."""
        self._connection_state = state
        self._connection_domain = domain
        self._connection_protocol = protocol
        self._refresh_connection_label()

    def _refresh_connection_label(self) -> None:
        colors = status_colors_for(self.config.theme)
        state = self._connection_state
        if state == "connected":
            text = f"●  Connecté — {self._connection_domain} ({self._connection_protocol})"
            color = colors["connected"]
        elif state == "connecting":
            text = "●  Connexion en cours…"
            color = colors["connecting"]
        else:
            text = "●  Déconnecté"
            color = colors["disconnected"]
        self.connection_label.setText(text)
        self.connection_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _on_report_issue(self) -> None:
        """Ouvre le navigateur sur un ticket GitHub prérempli plutôt que
        d'appeler l'API GitHub directement : ça éviterait d'avoir à embarquer
        un jeton dans chaque .exe/.flatpak distribué (extractible et
        exploitable par n'importe qui). L'utilisateur relit et clique
        "Submit" lui-même — rien n'est envoyé sans confirmation."""
        version = f"{CURRENT_VERSION}{_platform_suffix()}"
        log_lines = AppLogManager.instance().lines()
        excerpt = "\n".join(log_lines)[-MAX_LOG_EXCERPT_CHARS:]
        body = (
            f"**Version :** {version}\n"
            f"**Système :** {platform.platform()}\n\n"
            "**Description du problème :**\n(décrivez ici ce qui s'est passé et ce que vous attendiez)\n\n"
            "**Journal récent** — vérifiez qu'aucune information sensible (nom de domaine, "
            "identifiant…) n'apparaît ci-dessous avant d'envoyer :\n"
            f"```\n{excerpt}\n```\n"
        )
        url = f"{ISSUE_TRACKER_URL}?title={quote('Bug : ')}&body={quote(body)}"
        webbrowser.open(url)

    def _build_body(self) -> None:
        central = QWidget()
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 12, 0, 0)
        sidebar_layout.setSpacing(2)

        self.pages = QStackedWidget()
        self.create_accounts_page = CreateAccountsPage(
            self.ad_connection, self.config, self.audit_log, self.password_vault, self.session_id
        )
        self.migration_page = MigrationPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.depart_page = DepartPage(
            self.ad_connection, self.config, self.audit_log, self.password_vault, self.session_id
        )
        self.password_reset_page = PasswordResetPage(
            self.ad_connection, self.config, self.audit_log, self.password_vault, self.session_id
        )
        self.ad_explorer_page = ADExplorerPage(
            self.ad_connection, self.config, self.audit_log, self.password_vault, self.session_id
        )
        self.export_page = ExportPage(
            self.ad_connection, self.config, self.audit_log, self.password_vault, self.session_id
        )
        self.audit_page = AuditPage(self.audit_log)
        self.logs_page = LogViewWidget()
        self.settings_page = SettingsPage(
            self.config, self._on_config_saved, self.password_vault, ad_domain=self.ad_connection.domain
        )

        self.pages.addWidget(self.create_accounts_page)    # index 0
        self.pages.addWidget(self.migration_page)          # index 1
        self.pages.addWidget(self.depart_page)             # index 2
        self.pages.addWidget(self.password_reset_page)     # index 3
        self.pages.addWidget(self.ad_explorer_page)        # index 4
        self.pages.addWidget(self.export_page)             # index 5
        self.pages.addWidget(self.audit_page)              # index 6
        self.pages.addWidget(self.logs_page)               # index 7
        self.pages.addWidget(self.settings_page)           # index 8

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        # None = séparateur visuel (regroupe "Comptes/actions" vs "Système")
        nav_items = [
            ("Création de comptes", 0),
            ("Migration (fin d'année)", 1),
            ("Gestion des départs", 2),
            ("Réinit. mots de passe", 3),
            ("Explorateur AD", 4),
            ("Export (CSV / étiquettes)", 5),
            None,
            ("Journal d'actions", 6),
            ("Journal de l'application", 7),
            ("Paramètres", 8),
        ]
        for item in nav_items:
            if item is None:
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setObjectName("SidebarSeparator")
                sidebar_layout.addSpacing(8)
                sidebar_layout.addWidget(separator)
                sidebar_layout.addSpacing(8)
                continue
            label, index = item
            button = QPushButton(label)
            button.setObjectName("SidebarButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, i=index: self.pages.setCurrentIndex(i))
            self._nav_group.addButton(button)
            sidebar_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        sidebar_layout.addStretch()

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.pages)
        self.setCentralWidget(central)

    def _on_config_saved(self, config: AppConfig) -> None:
        self.config = config
        save_config(config)
        self.create_accounts_page.update_config(config)
        self.migration_page.update_config(config)
        self.depart_page.update_config(config)
        self.password_reset_page.update_config(config)
        self.ad_explorer_page.update_config(config)
        self.export_page.update_config(config)
        self.apply_theme()

    def _on_check_update(self) -> None:
        dlg = UpdateDialog(self)
        dlg.exec()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet_for(self.config.theme))
        self._refresh_connection_label()
