"""Écran de connexion à l'Active Directory (§3 du cahier des charges)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
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
from edusync_ad.core.config import AppConfig, save_config
from edusync_ad.core.crypto import (
    RememberedConnection,
    clear_remembered_connection,
    load_remembered_connection,
    save_remembered_connection,
)
from edusync_ad.core.multisite import (
    DomainProfile,
    MultisiteError,
    ensure_sites,
    find_profile,
    load_sites,
    save_sites,
)
from edusync_ad.ui.debug_console import DebugConsole
from edusync_ad.ui.log_manager import AppLogManager

logger = logging.getLogger("edusync_ad.login")

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
        except Exception as exc:
            # Ne doit normalement jamais arriver (bug de programmation plutôt
            # qu'un refus AD) — sans ce filet, le thread s'arrête en silence
            # et le bouton "Se connecter" reste bloqué indéfiniment.
            logger.exception("Erreur interne inattendue pendant la connexion")
            self.failed.emit(f"Erreur interne : {exc}")
        else:
            self.succeeded.emit(result)


class LoginDialog(QDialog):
    def __init__(
        self,
        parent=None,
        config: AppConfig | None = None,
        profile: DomainProfile | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("EduSync AD — Connexion")
        self.setMinimumWidth(440)

        self.config = config or AppConfig()
        # Profil M25 (multisite) à utiliser pour cette session : préremplit le
        # formulaire et re-sauvegarde les identifiants après connexion réussie.
        self._site_profile = profile
        self.ad_connection = ADConnection(
            verify_certificate=self.config.ldaps_verifier_certificat,
            ca_cert_path=self.config.ldaps_chemin_certificat_ca or None,
        )
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
        self.remember_password_checkbox = QCheckBox("Mémoriser aussi le mot de passe")
        self.remember_password_checkbox.setToolTip(
            "Le mot de passe est chiffré (AES-256) sur cette machine, mais reste\n"
            "moins sûr que de le ressaisir à chaque connexion. À réserver aux\n"
            "postes de confiance."
        )
        self.remember_password_checkbox.setEnabled(False)
        self.remember_checkbox.toggled.connect(self.remember_password_checkbox.setEnabled)
        self.remember_checkbox.toggled.connect(
            lambda checked: checked or self.remember_password_checkbox.setChecked(False)
        )
        self.debug_checkbox = QCheckBox("Mode debug (journal de connexion en direct)")
        self._debug_console: DebugConsole | None = None

        self.verify_cert_checkbox = QCheckBox("Vérifier le certificat du contrôleur en LDAPS (recommandé)")
        self.verify_cert_checkbox.setChecked(self.config.ldaps_verifier_certificat)
        self.verify_cert_checkbox.setToolTip(
            "Désactiver revient à accepter n'importe quel certificat en LDAPS — le chiffrement\n"
            "reste actif mais l'identité du contrôleur de domaine n'est plus vérifiée\n"
            "(risque d'interception). À ne faire qu'en connaissance de cause."
        )
        self.ca_cert_path_edit = QLineEdit(self.config.ldaps_chemin_certificat_ca)
        self.ca_cert_path_edit.setPlaceholderText(
            "Certificat racine de l'AD (.pem/.crt), optionnel — nécessaire si l'AD utilise sa propre autorité"
        )
        self.ca_cert_browse_button = QPushButton("Parcourir…")
        self.ca_cert_browse_button.clicked.connect(self._on_browse_ca_cert)
        ca_cert_row = QHBoxLayout()
        ca_cert_row.addWidget(self.ca_cert_path_edit)
        ca_cert_row.addWidget(self.ca_cert_browse_button)

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
        form.addRow("", self.remember_password_checkbox)
        form.addRow("", self.verify_cert_checkbox)
        form.addRow("Certificat CA (optionnel)", ca_cert_row)
        form.addRow("", self.debug_checkbox)

        status_layout = QHBoxLayout()
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(status_layout)
        layout.addWidget(self.connect_button)

        if self._site_profile is not None:
            self._prefill_profile(self._site_profile)
        elif not self._prefill_remembered_connection():
            # Aucune connexion mémorisée : on propose le premier site enregistré
            # plutôt qu'un formulaire vide (l'admin n'a rien à retaper).
            sites = ensure_sites()
            if sites:
                self._prefill_profile(sites[0], remember=False)

        app = QApplication.instance()
        if app is not None:
            # Filet de sécurité : évite un crash "QThread: Destroyed while
            # thread is still running" si l'app se ferme pendant la connexion.
            app.aboutToQuit.connect(self._on_app_quit)

    def _on_app_quit(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5000)

    def _prefill_remembered_connection(self) -> bool:
        remembered = load_remembered_connection()
        if not remembered:
            return False
        self.domain_edit.setText(remembered.domaine)
        self.controller_edit.setText(remembered.controleur)
        self.username_edit.setText(remembered.utilisateur)
        self.remember_checkbox.setChecked(True)
        if remembered.mot_de_passe is not None:
            self.password_edit.setText(remembered.mot_de_passe)
            self.remember_password_checkbox.setChecked(True)
        return True

    def _prefill_profile(self, profile: DomainProfile, *, remember: bool = True) -> None:
        """Préremplit l'écran avec un profil de domaine (M25 multisite)."""
        self.domain_edit.setText(profile.domain)
        self.controller_edit.setText(profile.controller)
        self.username_edit.setText(profile.username)
        self.remember_checkbox.setChecked(remember)
        if profile.password and profile.remember_password:
            self.password_edit.setText(profile.password)
            if remember:
                self.remember_password_checkbox.setChecked(True)
        self.verify_cert_checkbox.setChecked(profile.verify_certificate)
        self.ca_cert_path_edit.setText(profile.ca_cert_path)

    def _update_site_profile(self) -> None:
        """Re-sauvegarde le profil M25 avec les identifiants réellement acceptés par l'AD.

        Le champ « nom de domaine » est volontairement laissé tel quel : c'est
        l'identité du site. Si l'administrateur a tapé un autre domaine pour
        cette session, le profil ne bascule pas en douce — le sélecteur affiche
        alors « site non enregistré » et le site s'ajoute via « Domaines… ».
        """
        if self._site_profile is None:
            return
        try:
            profiles = load_sites()
        except MultisiteError:
            # Fichier illisible : on ne l'écrase pas à la volée.
            return
        profile = find_profile(profiles, self._site_profile.id)
        if profile is None:
            return
        profile.controller = self.controller_edit.text().strip()
        profile.username = self.username_edit.text().strip()
        profile.remember_password = self.remember_password_checkbox.isChecked()
        profile.password = self.password_edit.text() if profile.remember_password else ""
        profile.verify_certificate = self.verify_cert_checkbox.isChecked()
        profile.ca_cert_path = self.ca_cert_path_edit.text().strip()
        try:
            save_sites(profiles)
        except OSError:
            logger.warning("Impossible de mettre à jour le profil de domaine %s", profile.id)

    def _on_browse_ca_cert(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Certificat racine de l'AD", "", "Certificats (*.pem *.crt *.cer);;Tous les fichiers (*)"
        )
        if path:
            self.ca_cert_path_edit.setText(path)

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

        self.ad_connection.verify_certificate = self.verify_cert_checkbox.isChecked()
        self.ad_connection.ca_cert_path = self.ca_cert_path_edit.text().strip() or None

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
                    mot_de_passe=(
                        self.password_edit.text()
                        if self.remember_password_checkbox.isChecked()
                        else None
                    ),
                )
            )
        else:
            clear_remembered_connection()

        self._update_site_profile()

        self.config.ldaps_verifier_certificat = self.verify_cert_checkbox.isChecked()
        self.config.ldaps_chemin_certificat_ca = self.ca_cert_path_edit.text().strip()
        save_config(self.config)

        if result.warning:
            QMessageBox.warning(self, "Avertissement", result.warning)

        self.accept()

    def _on_connect_failed(self, message: str) -> None:
        self._set_status("disconnected", "Déconnecté")
        self.connect_button.setEnabled(True)
        QMessageBox.critical(self, "Échec de connexion", message)
