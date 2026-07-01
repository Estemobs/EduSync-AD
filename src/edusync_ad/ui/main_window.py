"""Fenêtre principale : bandeau (connexion + mode simulation), sidebar, pages."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
from edusync_ad.ui.audit_page import AuditPage
from edusync_ad.ui.modules.create_accounts_page import CreateAccountsPage
from edusync_ad.ui.modules.depart_page import DepartPage
from edusync_ad.ui.modules.migration_page import MigrationPage
from edusync_ad.ui.settings_page import SettingsPage
from edusync_ad.ui.theme import stylesheet_for


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
        self.session_id = new_session_id()

        self.setWindowTitle("EduSync AD")
        self.resize(1100, 720)

        self._build_top_bar()
        self._build_body()
        self.apply_theme()

    def _build_top_bar(self) -> None:
        top_bar = QWidget()
        top_bar.setObjectName("TopBar")
        layout = QHBoxLayout(top_bar)

        domain_text = self.ad_connection.domain or ""
        protocol_text = "LDAPS" if self.ad_connection.used_ldaps else "LDAP (non chiffré)"
        self.connection_label = QLabel(f"●  Connecté — {domain_text} ({protocol_text})")
        self.connection_label.setStyleSheet("color: #6fe08a; font-weight: 600;")
        layout.addWidget(self.connection_label)
        layout.addStretch()

        self.simulation_button = QPushButton("Mode simulation : désactivé")
        self.simulation_button.setCheckable(True)
        self.simulation_button.toggled.connect(self._on_simulation_toggled)
        layout.addWidget(self.simulation_button)

        self.setMenuWidget(top_bar)

    def _on_simulation_toggled(self, checked: bool) -> None:
        self.ad_connection.dry_run = checked
        self.simulation_button.setText(
            "Mode simulation : activé" if checked else "Mode simulation : désactivé"
        )
        self.simulation_button.setStyleSheet("background-color: #e0a72b; color: black;" if checked else "")

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
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.migration_page = MigrationPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.audit_page = AuditPage(self.audit_log)
        self.settings_page = SettingsPage(self.config, self._on_config_saved)

        self.pages.addWidget(self.create_accounts_page)   # index 0
        self.pages.addWidget(self.migration_page)          # index 1
        self.pages.addWidget(self.audit_page)              # index 2
        self.pages.addWidget(self.settings_page)           # index 3

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        nav_items = [
            ("Création de comptes", 0),
            ("Migration (fin d'année)", 1),
            ("Journal d'actions", 2),
            ("Paramètres", 3),
        ]
        for label, index in nav_items:
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
        self.apply_theme()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet_for(self.config.theme))
