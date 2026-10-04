"""Fenêtre principale : bandeau (connexion, mises à jour, rapport de bug), sidebar, pages."""

from __future__ import annotations

import platform
import webbrowser
from urllib.parse import quote

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.async_connection import AsyncADConnection
from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.audit import AuditLog, new_session_id
from edusync_ad.core.config import AppConfig, save_config
from edusync_ad.core.multisite import ensure_sites, find_profile, profile_for_domain
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.core.updater import CURRENT_VERSION, check_for_update
from edusync_ad.ui.audit_page import AuditPage
from edusync_ad.ui.domain_dialog import DomainsDialog
from edusync_ad.ui.log_manager import AppLogManager
from edusync_ad.ui.log_view_widget import LogViewWidget
from edusync_ad.ui.modules.ad_explorer_page import ADExplorerPage
from edusync_ad.ui.modules.create_accounts_page import CreateAccountsPage
from edusync_ad.ui.modules.depart_page import DepartPage
from edusync_ad.ui.modules.export_page import ExportPage
from edusync_ad.ui.modules.migration_page import MigrationPage
from edusync_ad.ui.modules.password_reset_page import PasswordResetPage
from edusync_ad.ui.modules.profiles_page import ProfilesPage
from edusync_ad.ui.modules.home_dirs_page import HomeDirsPage
from edusync_ad.ui.modules.quotas_page import QuotasPage
from edusync_ad.ui.modules.logon_hours_page import LogonHoursPage
from edusync_ad.ui.modules.logon_scripts_page import LogonScriptsPage
from edusync_ad.ui.modules.class_spaces_page import ClassSpacesPage
from edusync_ad.ui.modules.m365_page import M365Page
from edusync_ad.ui.modules.exchange_page import ExchangePage
from edusync_ad.ui.modules.rds_page import RDSPage
from edusync_ad.ui.modules.advanced_io_page import AdvancedIOPage
from edusync_ad.ui.modules.templates_page import TemplatesPage
from edusync_ad.ui.modules.label_studio_page import LabelStudioPage
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


# Référence conservée aux threads de vérification de mise à jour qui survivent
# à leur fenêtre : sans elle, le changement de domaine (M25) recrée la fenêtre
# pendant que le thread tourne → « QThread: Destroyed while thread is running ».
_detached_workers: set[_StartupUpdateCheckWorker] = set()


def _keep_worker_alive(worker: _StartupUpdateCheckWorker) -> None:
    _detached_workers.add(worker)
    worker.finished.connect(lambda w=worker: _detached_workers.discard(w))


class MainWindow(QMainWindow):
    #: Demande de changement de site (M25) — émet l'identifiant du profil de
    #: domaine choisi ; `app.py` referme la fenêtre et rouvre la connexion.
    site_switched = pyqtSignal(str)

    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.async_ad = AsyncADConnection(ad_connection)
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

        # Sélecteur de domaine (M25 multisite) — un seul domaine connecté à la fois.
        self.domain_combo = QComboBox()
        self.domain_combo.setMinimumWidth(210)
        self.domain_combo.setToolTip(
            "Domaine AD actif.\nChoisir un autre site referme la session en cours\n"
            "et rouvre l'écran de connexion avec le profil de ce site."
        )
        self.domain_combo.currentIndexChanged.connect(self._on_domain_selected)
        layout.addWidget(self.domain_combo)
        self._populate_domain_combo()

        manage_domains_btn = QPushButton("Domaines…")
        manage_domains_btn.setToolTip("Ajouter, modifier ou supprimer les domaines gérés")
        manage_domains_btn.clicked.connect(self._on_manage_domains)
        layout.addWidget(manage_domains_btn)

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

    # -- Multisite (M25) -----------------------------------------------------

    def _populate_domain_combo(self) -> None:
        """Remplit le sélecteur de domaine et repère le profil de la session en cours."""
        profiles = ensure_sites()
        self._site_profiles = profiles
        active = profile_for_domain(profiles, self.ad_connection.domain)
        self._active_site_id = active.id if active is not None else ""

        self.domain_combo.blockSignals(True)
        try:
            self.domain_combo.clear()
            if active is None:
                current = self.ad_connection.domain or ""
                suffix = f"{current} (site non enregistré)" if current else "(site non enregistré)"
                self.domain_combo.addItem(suffix, "")
            for profile in profiles:
                self.domain_combo.addItem(profile.display_name, profile.id)
            self.domain_combo.setCurrentIndex(self._row_for_active_site())
            self.domain_combo.setEnabled(bool(profiles))
        finally:
            self.domain_combo.blockSignals(False)

    def _row_for_active_site(self) -> int:
        for row in range(self.domain_combo.count()):
            if self.domain_combo.itemData(row) == self._active_site_id:
                return row
        return 0

    def _revert_domain_combo(self) -> None:
        self.domain_combo.blockSignals(True)
        try:
            self.domain_combo.setCurrentIndex(self._row_for_active_site())
        finally:
            self.domain_combo.blockSignals(False)

    def _on_domain_selected(self, index: int) -> None:
        site_id = self.domain_combo.itemData(index)
        site_id = site_id if isinstance(site_id, str) else ""
        if not site_id or site_id == self._active_site_id:
            return
        profile = find_profile(self._site_profiles, site_id)
        if profile is None:
            self._revert_domain_combo()
            return
        answer = QMessageBox.question(
            self,
            "Changer de domaine",
            f"Passer au site « {profile.display_name} » ?\n\n"
            "Un seul domaine est connecté à la fois : la session en cours sera fermée "
            "et l'écran de connexion rouvert avec les identifiants de ce site.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._revert_domain_combo()
            return
        self._active_site_id = site_id
        self.site_switched.emit(site_id)

    def _on_manage_domains(self) -> None:
        dialog = DomainsDialog(
            self,
            profiles=list(self._site_profiles),
            current_domain=self.ad_connection.domain or "",
        )
        dialog.exec()
        # Les ajouts/suppressions sont persistés par le dialogue : on rebâtit
        # le sélecteur même s'il a été fermé sans « Enregistrer ».
        self._populate_domain_combo()

    def closeEvent(self, event) -> None:
        worker = self._update_check_worker
        if worker is not None and worker.isRunning():
            _keep_worker_alive(worker)
        self._update_check_worker = None
        super().closeEvent(event)

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
            self.ad_connection, self.config, self.audit_log, self.password_vault, self.session_id,
            async_ad=self.async_ad
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
        self.profiles_page = ProfilesPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.home_dirs_page = HomeDirsPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.quotas_page = QuotasPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.logon_hours_page = LogonHoursPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.logon_scripts_page = LogonScriptsPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.class_spaces_page = ClassSpacesPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.m365_page = M365Page(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.exchange_page = ExchangePage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.rds_page = RDSPage(self.config, self.audit_log, self.session_id)
        self.advanced_io_page = AdvancedIOPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.templates_page = TemplatesPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
        )
        self.label_studio_page = LabelStudioPage(
            self.ad_connection, self.config, self.audit_log, self.session_id
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
        self.pages.addWidget(self.profiles_page)           # index 6
        self.pages.addWidget(self.home_dirs_page)           # index 7
        self.pages.addWidget(self.quotas_page)              # index 8
        self.pages.addWidget(self.logon_hours_page)         # index 9
        self.pages.addWidget(self.logon_scripts_page)       # index 10
        self.pages.addWidget(self.class_spaces_page)        # index 11
        self.pages.addWidget(self.m365_page)                # index 12
        self.pages.addWidget(self.exchange_page)            # index 13
        self.pages.addWidget(self.rds_page)                 # index 14
        self.pages.addWidget(self.advanced_io_page)         # index 15
        self.pages.addWidget(self.templates_page)           # index 16
        self.pages.addWidget(self.label_studio_page)         # index 17
        self.pages.addWidget(self.audit_page)              # index 18
        self.pages.addWidget(self.logs_page)               # index 19
        self.pages.addWidget(self.settings_page)           # index 20

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
            ("Profils utilisateurs", 6),
            ("Dossiers personnels", 7),
            ("Quotas de disque", 8),
            ("Heures de connexion", 9),
            ("Scripts de session", 10),
            ("Espaces de classe", 11),
            ("Microsoft 365", 12),
            ("Microsoft Exchange", 13),
            ("RDS / Bureau à distance", 14),
            ("Import / Export avancés", 15),
            ("Modèles de groupes", 16),
            ("Étiquettes / trombinoscopes", 17),
            None,
            ("Journal d'actions", 18),
            ("Journal de l'application", 19),
            ("Paramètres", 20),
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
        self.profiles_page.update_config(config)
        self.home_dirs_page.update_config(config)
        self.quotas_page.update_config(config)
        self.logon_hours_page.update_config(config)
        self.logon_scripts_page.update_config(config)
        self.class_spaces_page.update_config(config)
        self.m365_page.update_config(config)
        self.exchange_page.update_config(config)
        self.rds_page.update_config(config)
        self.advanced_io_page.update_config(config)
        self.templates_page.update_config(config)
        self.label_studio_page.update_config(config)
        self.apply_theme()

    def _on_check_update(self) -> None:
        dlg = UpdateDialog(self)
        dlg.exec()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet_for(self.config.theme))
        self._refresh_connection_label()
