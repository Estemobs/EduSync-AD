"""Écran de connexion à l'Active Directory (§3 du cahier des charges)."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from edusync_ad.core.ad.connection import ADConnection, ConnectResult
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.crypto import (
    RememberedConnection,
    clear_remembered_connection,
    load_remembered_connection,
    save_remembered_connection,
)
from edusync_ad.ui.debug_console import DebugConsole
from edusync_ad.ui.log_manager import AppLogManager

STATUS_COLORS = {"disconnected": "#d24343", "connecting": "#e0a72b", "connected": "#2fa84f"}


class _ConnectWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        ad_connection: ADConnection,
        domain: str,
        controller: str,
        username: str,
        password: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._ad = ad_connection
        self._domain = domain
        self._controller = controller
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            result = self._ad.connect(self._domain, self._controller, self._username, self._password)
        except ADError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class LoginDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EduSync AD — Connexion")
        self.setMinimumWidth(440)

        self.ad_connection = ADConnection()
        self._worker: _ConnectWorker | None = None

        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("lycee-victor-hugo.local")
        self.controller_edit = QLineEdit()
        self.controller_edit.setPlaceholderText("10.0.0.5 ou dc01.lycee-victor-hugo.local")
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("admin")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_checkbox = QCheckBox("Mémoriser la connexion")
        self.debug_checkbox = QCheckBox("Mode debug (journal de connexion en direct)")
        self._debug_console: DebugConsole | None = None

        self.status_dot = QLabel("●")
        self.status_label = QLabel("Déconnecté")
        self._set_status("disconnected", "Déconnecté")

        self.connect_button = QPushButton("Se connecter")
        self.connect_button.clicked.connect(self._on_connect_clicked)

        form = QFormLayout()
        form.addRow("Nom de domaine", self.domain_edit)
        form.addRow("Contrôleur de domaine", self.controller_edit)
        form.addRow("Nom d'utilisateur", self.username_edit)
        form.addRow("Mot de passe", self.password_edit)
        form.addRow("", self.remember_checkbox)
        form.addRow("", self.debug_checkbox)

        status_layout = QHBoxLayout()
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(status_layout)
        layout.addWidget(self.connect_button)

        self._prefill_remembered_connection()

    def _prefill_remembered_connection(self) -> None:
        remembered = load_remembered_connection()
        if remembered:
            self.domain_edit.setText(remembered.domaine)
            self.controller_edit.setText(remembered.controleur)
            self.username_edit.setText(remembered.utilisateur)
            self.remember_checkbox.setChecked(True)

    def _set_status(self, state: str, text: str) -> None:
        self.status_dot.setStyleSheet(f"color: {STATUS_COLORS.get(state, '#999')}; font-size: 16px;")
        self.status_label.setText(text)

    def _on_connect_clicked(self) -> None:
        domain = self.domain_edit.text().strip()
        controller = self.controller_edit.text().strip()
        username = self.username_edit.text().strip()
        password = self.password_edit.text()

        if not domain or not controller or not username or not password:
            QMessageBox.warning(self, "Champs requis", "Tous les champs sont obligatoires.")
            return

        if self.debug_checkbox.isChecked():
            if self._debug_console is None:
                self._debug_console = DebugConsole(self)
            self._debug_console.start()
            self._debug_console.show()
            self._debug_console.raise_()
            self._debug_console.activateWindow()
        else:
            AppLogManager.instance().set_debug(False)

        self.connect_button.setEnabled(False)
        self._set_status("connecting", "Connexion en cours…")

        self._worker = _ConnectWorker(self.ad_connection, domain, controller, username, password)
        self._worker.succeeded.connect(self._on_connect_succeeded)
        self._worker.failed.connect(self._on_connect_failed)
        self._worker.start()

    def _on_connect_succeeded(self, result: ConnectResult) -> None:
        suffix = "" if result.used_ldaps else " (LDAP non chiffré)"
        self._set_status("connected", f"Connecté{suffix}")
        self.connect_button.setEnabled(True)

        if self.remember_checkbox.isChecked():
            save_remembered_connection(
                RememberedConnection(
                    domaine=self.domain_edit.text().strip(),
                    controleur=self.controller_edit.text().strip(),
                    utilisateur=self.username_edit.text().strip(),
                )
            )
        else:
            clear_remembered_connection()

        if result.warning:
            QMessageBox.warning(self, "Avertissement", result.warning)

        self.accept()

    def _on_connect_failed(self, message: str) -> None:
        self._set_status("disconnected", "Déconnecté")
        self.connect_button.setEnabled(True)
        QMessageBox.critical(self, "Échec de connexion", message)
