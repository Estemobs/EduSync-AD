"""Page M27 — Portail auto-service : réglages, serveur et demandes de compte.

Trois volets :

* **Serveur** — interface d'écoute, durée de validité des codes, politique de
  mot de passe appliquée aux réinitialisations, OU d'accueil des demandes,
  démarrage/arrêt manuel ou automatique ;
* **Demandes** — file d'attente des pré-inscriptions soumises depuis le
  portail, avec validation (création du compte) ou refus ;
* **Aide** — texte destiné à être affiché ou imprimé pour les usagers.

Le serveur HTTP vit dans ``core/portal_server.py`` (sans PyQt) : cette page
n'en assurait que le cycle de vie, comme la page Étiquettes assure celui de
son SMTP.
"""

from __future__ import annotations

import webbrowser

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.mailer import load_mail_config
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.core.portal import (
    POLITIQUE_LABELS,
    PROFIL_ELEVE,
    STATUT_APPROUVEE,
    STATUT_EN_ATTENTE,
    STATUT_REFUSEE,
    AdDirectory,
    Demande,
    PortalConfig,
    PortalError,
    PortalService,
    PortalStore,
    load_portal_config,
    save_portal_config,
)
from edusync_ad.core.portal_server import PortalServer
from edusync_ad.core.rbac import RBACPolicy

STATUT_LABELS = {
    STATUT_EN_ATTENTE: "En attente",
    STATUT_APPROUVEE: "Approuvée",
    STATUT_REFUSEE: "Refusée",
}

#: Colonne portant l'identifiant de la demande dans le tableau.
COL_ID = 0


class CredentialsDialog(QDialog):
    """Affiche les identifiants générés lors de la validation d'une demande."""

    def __init__(self, infos: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compte créé")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Le compte a été créé dans l'annuaire :"))
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for key, label in (
            ("identifiant", "Identifiant"),
            ("mot_de_passe", "Mot de passe"),
            ("mail", "Adresse de messagerie"),
            ("dn", "Emplacement (DN)"),
        ):
            edit = QLineEdit(str(infos.get(key) or ""))
            edit.setReadOnly(True)
            if key == "mot_de_passe":
                edit.setStyleSheet("font-weight: 700; letter-spacing: .12em;")
            self.fields[key] = edit
            form.addRow(label, edit)
        layout.addLayout(form)
        layout.addWidget(
            QLabel(
                "Notez ces informations : le mot de passe est également conservé "
                "dans le coffre de l'application."
            )
        )

        buttons = QDialogButtonBox()
        copy_btn = buttons.addButton(
            "Copier les identifiants", QDialogButtonBox.ButtonRole.ActionRole
        )
        copy_btn.clicked.connect(self._copy)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

    def _copy(self) -> None:
        text = "\n".join(
            f"{label} : {self.fields[key].text()}"
            for key, label in (
                ("identifiant", "Identifiant"),
                ("mot_de_passe", "Mot de passe"),
                ("mail", "Adresse de messagerie"),
            )
        )
        QApplication.clipboard().setText(text)


class PortalPage(QWidget):
    """Page M27 — Portail auto-service."""

    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        session_id: str,
        password_vault: PasswordVault,
        rbac: RBACPolicy | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.session_id = session_id
        self.password_vault = password_vault
        self.rbac = rbac

        self.portal_config: PortalConfig = load_portal_config()
        self.store: PortalStore | None = PortalStore()
        self.service: PortalService | None = None
        self.server: PortalServer | None = None
        self._ous_loaded = False

        self._build_ui()
        self._load_form()
        # Démarrage automatique (réglage « actif ») — reporté d'un tick pour
        # que la fenêtre principale finisse d'être construite.
        if self.portal_config.actif:
            QTimer.singleShot(0, self.start_server)

    # -- Cycle de vie --------------------------------------------------------

    def update_config(self, config: AppConfig) -> None:
        self.config = config
        if self.service is not None:
            self.service.app_config = config

    def stop_server(self) -> None:
        """Arrêt du serveur HTTP (demandé par ``MainWindow.closeEvent``)."""
        if self.server is not None:
            self.server.stop()
            self.server = None
        self._refresh_status(running=False)

    def shutdown(self) -> None:
        """Arrêt définitif : serveur HTTP puis magasin SQLite (fin de session)."""
        self.stop_server()
        if self.store is not None:
            try:
                self.store.close()
            except Exception:  # noqa: BLE001 - fermeture, rien à sauver
                pass
            self.store = None
        self.service = None

    def closeEvent(self, event) -> None:  # noqa: D102
        self.shutdown()
        super().closeEvent(event)

    # -- Construction UI -----------------------------------------------------

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_server_tab(), "Serveur")
        self.tabs.addTab(self._build_requests_tab(), "Demandes")
        self.tabs.addTab(self._build_help_tab(), "Aide")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)

    def _build_server_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        listen_group = QGroupBox("Publication du portail")
        listen_form = QFormLayout(listen_group)
        self.auto_check = QCheckBox("Démarrer le serveur en même temps que la session")
        listen_form.addRow(self.auto_check)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("127.0.0.1 (cette machine) ou 0.0.0.0 (réseau interne)")
        listen_form.addRow("Interface d'écoute :", self.host_edit)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        listen_form.addRow("Port :", self.port_spin)
        self.title_edit = QLineEdit()
        listen_form.addRow("Titre affiché :", self.title_edit)
        layout.addWidget(listen_group)

        security_group = QGroupBox("Codes de vérification")
        security_form = QFormLayout(security_group)
        self.code_minutes_spin = QSpinBox()
        self.code_minutes_spin.setRange(1, 60)
        self.code_minutes_spin.setSuffix(" minutes")
        security_form.addRow("Validité du code :", self.code_minutes_spin)
        self.attempts_spin = QSpinBox()
        self.attempts_spin.setRange(1, 10)
        security_form.addRow("Tentatives par code :", self.attempts_spin)
        self.sends_spin = QSpinBox()
        self.sends_spin.setRange(1, 20)
        security_form.addRow("Envois / 15 min :", self.sends_spin)
        self.policy_combo = QComboBox()
        for key, label in POLITIQUE_LABELS.items():
            self.policy_combo.addItem(label, key)
        security_form.addRow("Politique de mot de passe :", self.policy_combo)
        layout.addWidget(security_group)

        requests_group = QGroupBox("Demandes de création de compte")
        requests_form = QFormLayout(requests_group)
        self.ou_combo = QComboBox()
        self.ou_combo.setEditable(True)
        self.ou_combo.setPlaceholderText("OU=Eleves,DC=lycee,DC=local")
        refresh_ou = QPushButton("Lister les OUs…")
        refresh_ou.setToolTip("Charge la liste des OU du domaine connecté")
        refresh_ou.clicked.connect(self._load_ous)
        ou_row = QHBoxLayout()
        ou_row.addWidget(self.ou_combo, 1)
        ou_row.addWidget(refresh_ou)
        requests_form.addRow("OU d'accueil", ou_row)
        self.notify_check = QCheckBox(
            "Envoyer les identifiants générés au demandeur lors de la validation"
        )
        requests_form.addRow(self.notify_check)
        layout.addWidget(requests_group)

        self.smtp_hint = QLabel()
        self.smtp_hint.setWordWrap(True)
        self.smtp_hint.setStyleSheet("color: #b45309;")
        layout.addWidget(self.smtp_hint)

        controls = QHBoxLayout()
        self.save_btn = QPushButton("💾 Enregistrer les réglages")
        self.save_btn.clicked.connect(self._save_form)
        controls.addWidget(self.save_btn)
        self.start_btn = QPushButton("▶ Démarrer le serveur")
        self.start_btn.clicked.connect(self.start_server)
        controls.addWidget(self.start_btn)
        self.stop_btn = QPushButton("■ Arrêter")
        self.stop_btn.clicked.connect(self.stop_clicked)
        controls.addWidget(self.stop_btn)
        self.open_btn = QPushButton("🌐 Ouvrir dans le navigateur")
        self.open_btn.clicked.connect(self._open_in_browser)
        controls.addWidget(self.open_btn)
        controls.addStretch()
        layout.addLayout(controls)

        self.status_label = QLabel("● Serveur arrêté")
        self.status_label.setStyleSheet("font-weight: 600; color: #666;")
        layout.addWidget(self.status_label)
        self.status_hint = QLabel(
            "Le portail se visite depuis un navigateur : chaque usager y demande "
            "un code, reçoit le courriel, puis change son mot de passe ou consulte "
            "son identifiant."
        )
        self.status_hint.setWordWrap(True)
        self.status_hint.setStyleSheet("color: #888;")
        layout.addWidget(self.status_hint)
        layout.addStretch()
        return widget

    def _build_requests_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QHBoxLayout()
        header.addWidget(QLabel("Demandes soumises depuis le portail :"))
        header.addStretch()
        self.statut_combo = QComboBox()
        self.statut_combo.addItem("En attente", STATUT_EN_ATTENTE)
        self.statut_combo.addItem("Approuvées", STATUT_APPROUVEE)
        self.statut_combo.addItem("Refusées", STATUT_REFUSEE)
        self.statut_combo.addItem("Toutes", "")
        self.statut_combo.currentIndexChanged.connect(lambda _index: self.refresh_requests())
        header.addWidget(self.statut_combo)
        refresh_btn = QPushButton("Actualiser")
        refresh_btn.clicked.connect(self.refresh_requests)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Nom", "Profil", "Classe / service", "Adresse mail", "Motif", "Déposée le"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        self.approve_btn = QPushButton("✔ Valider et créer le compte…")
        self.approve_btn.clicked.connect(self._approve_selected)
        actions.addWidget(self.approve_btn)
        self.refuse_btn = QPushButton("✖ Refuser…")
        self.refuse_btn.clicked.connect(self._refuse_selected)
        actions.addWidget(self.refuse_btn)
        actions.addStretch()
        self.pending_label = QLabel("0 demande(s) en attente")
        actions.addWidget(self.pending_label)
        layout.addLayout(actions)
        return widget

    def _build_help_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel("À afficher aux usagers")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(title)
        text = QLabel(
            "<b>Portail auto-service</b><br>"
            "Depuis le navigateur de l'établissement (adresse communiquée par "
            "l'administrateur) :<br><br>"
            "• <b>Réinitialiser son mot de passe</b> — saisir son identifiant ou son "
            "adresse mail, puis le code reçu par courriel ;<br>"
            "• <b>Consulter son identifiant</b> — même vérification, utile pour un "
            "élève qui ne retrouve plus son identifiant ;<br>"
            "• <b>Demander un compte</b> — l'administrateur valide la demande "
            "depuis cette application, les identifiants sont ensuite transmis."
            "<br><br>Un code expire au bout de quelques minutes et ne sert qu'une "
            "seule fois. Après trop de tentatives, il faut en demander un nouveau."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()
        return widget

    # -- Formulaire ----------------------------------------------------------

    def _load_form(self) -> None:
        cfg = self.portal_config
        self.auto_check.setChecked(cfg.actif)
        self.host_edit.setText(cfg.hote)
        self.port_spin.setValue(int(cfg.port))
        self.title_edit.setText(cfg.titre)
        self.code_minutes_spin.setValue(int(cfg.code_minutes))
        self.attempts_spin.setValue(int(cfg.tentatives_max))
        self.sends_spin.setValue(int(cfg.envois_max))
        index = self.policy_combo.findData(cfg.politique)
        self.policy_combo.setCurrentIndex(index if index >= 0 else 0)
        self.ou_combo.setCurrentText(cfg.ou_accueil)
        self.notify_check.setChecked(cfg.notification_mail)
        self._refresh_smtp_hint()
        self._refresh_status(running=False)

    def _form_config(self) -> PortalConfig:
        return PortalConfig(
            actif=self.auto_check.isChecked(),
            hote=self.host_edit.text().strip() or "127.0.0.1",
            port=int(self.port_spin.value()),
            code_minutes=int(self.code_minutes_spin.value()),
            tentatives_max=int(self.attempts_spin.value()),
            envois_max=int(self.sends_spin.value()),
            politique=str(self.policy_combo.currentData() or PROFIL_ELEVE),
            ou_accueil=self.ou_combo.currentText().strip(),
            notification_mail=self.notify_check.isChecked(),
            titre=self.title_edit.text().strip() or "Portail auto-service",
        )

    def _save_form(self) -> bool:
        config = self._form_config()
        try:
            save_portal_config(config)
        except PortalError as exc:
            QMessageBox.warning(self, "Réglages du portail", str(exc))
            return False
        self.portal_config = config
        if self.service is not None:
            self.service.config = config
        self.save_btn.setText("✓ Enregistré !")
        QTimer.singleShot(1600, lambda: self.save_btn.setText("💾 Enregistrer les réglages"))
        return True

    def _refresh_smtp_hint(self) -> None:
        mail_cfg = load_mail_config()
        errors = mail_cfg.validate()
        if errors:
            self.smtp_hint.setText(
                "⚠ SMTP non configuré : aucun code ne pourra être envoyé. "
                "Renseignez-le dans « Étiquettes / trombinoscopes » → onglet "
                "« Envoi par mail »."
            )
        else:
            self.smtp_hint.setText(
                f"✓ SMTP prêt ({mail_cfg.host}:{mail_cfg.port})."
            )
            self.smtp_hint.setStyleSheet("color: #1f9d55;")

    def _load_ous(self) -> None:
        """ Liste les OU du domaine pour pré-remplir l'OU d'accueil."""
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain or "")
        if not base_dn:
            QMessageBox.information(self, "OU d'accueil", "Aucun domaine connecté.")
            return
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except Exception as exc:  # noqa: BLE001 - affiché à l'opérateur
            QMessageBox.warning(self, "OU d'accueil", f"Lecture des OU impossible : {exc}")
            return
        current = self.ou_combo.currentText().strip()
        self.ou_combo.clear()
        for dn, name in ous:
            self.ou_combo.addItem(dn, name)
        if current:
            self.ou_combo.setCurrentText(current)
        self._ous_loaded = True

    # -- Serveur -------------------------------------------------------------

    def _ensure_service(self) -> PortalService:
        if self.store is None:
            self.store = PortalStore()
        if self.service is None:
            domain = self.ad_connection.domain or ""
            self.service = PortalService(
                config=self.portal_config,
                app_config=self.config,
                directory=AdDirectory(self.ad_connection, domain),
                store=self.store,
                smtp_config=load_mail_config(),
                audit_path=self.audit_log.path,
                domain=domain,
            )
        else:
            self.service.config = self.portal_config
            self.service.smtp_config = load_mail_config()
        return self.service

    def _may_run(self) -> bool:
        """Garde-fou RBAC (M26) : le portail modifie des mots de passe."""
        if self.rbac is None:
            return True
        if self.rbac.has_any(("reset_password", "create_user")):
            return True
        QMessageBox.warning(
            self,
            "Portail auto-service",
            "Votre rôle ne permet pas de publier le portail "
            "(réinitialisation de mots de passe / création de comptes).",
        )
        return False

    def start_server(self) -> None:
        if not self._may_run():
            return
        if not self._save_form():
            return
        if self.server is not None and self.server.running:
            self._refresh_status(running=True)
            return
        service = self._ensure_service()
        server = PortalServer(
            service, host=self.portal_config.hote, port=self.portal_config.port
        )
        try:
            server.start()
        except PortalError as exc:
            self.server = None
            self._refresh_status(running=False, error=str(exc))
            QMessageBox.critical(self, "Portail auto-service", str(exc))
            return
        self.server = server
        self._refresh_status(running=True)

    def stop_clicked(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None
        self._refresh_status(running=False)

    def _refresh_status(self, *, running: bool, error: str = "") -> None:
        if error:
            self.status_label.setText(f"● {error}")
            self.status_label.setStyleSheet("font-weight: 600; color: #b91c1c;")
        elif running and self.server is not None:
            url = self.server.url
            self.status_label.setText(f"● En écoute — {url}")
            self.status_label.setStyleSheet("font-weight: 600; color: #1f9d55;")
        else:
            self.status_label.setText("● Serveur arrêté")
            self.status_label.setStyleSheet("font-weight: 600; color: #666;")
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.open_btn.setEnabled(running)

    def _open_in_browser(self) -> None:
        if self.server is not None and self.server.running:
            webbrowser.open(self.server.url)

    # -- Demandes ------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self.refresh_requests()

    def _selected_demande(self) -> Demande | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, COL_ID)
        if item is None:
            return None
        demande_id = item.data(Qt.ItemDataRole.UserRole)
        service = self._ensure_service()
        return service.store.get_demande(str(demande_id))

    def refresh_requests(self) -> None:
        service = self._ensure_service()
        statut = str(self.statut_combo.currentData() or "")
        demandes = service.list_requests(statut or None)
        self.table.setRowCount(0)
        for demande in demandes:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [
                demande.nom_complet,
                "Élève" if demande.profil == PROFIL_ELEVE else "Personnel",
                demande.classe,
                demande.mail,
                demande.motif,
                demande.created_at.replace("T", " ")[:16],
            ]
            for col, value in enumerate(cells):
                cell = QTableWidgetItem(str(value))
                if col == COL_ID:
                    cell.setData(Qt.ItemDataRole.UserRole, demande.id)
                self.table.setItem(row, col, cell)
        attente = len(service.list_requests(STATUT_EN_ATTENTE))
        self.pending_label.setText(f"{attente} demande(s) en attente")
        self.approve_btn.setEnabled(statut in ("", STATUT_EN_ATTENTE))
        self.refuse_btn.setEnabled(statut in ("", STATUT_EN_ATTENTE))

    def _approve_selected(self) -> None:
        demande = self._selected_demande()
        if demande is None:
            QMessageBox.information(self, "Demandes", "Sélectionnez d'abord une demande.")
            return
        reply = QMessageBox.question(
            self,
            "Valider la demande",
            f"Créer le compte de « {demande.nom_complet} » "
            f"({('élève' if demande.profil == PROFIL_ELEVE else 'personnel')}) ?\n\n"
            "L'identifiant et le mot de passe seront générés automatiquement.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        service = self._ensure_service()
        try:
            infos = service.approve_request(demande.id)
        except PortalError as exc:
            QMessageBox.warning(self, "Demandes", str(exc))
            self.refresh_requests()
            return
        self.password_vault.store(str(infos["identifiant"]), str(infos["mot_de_passe"]))
        dialog = CredentialsDialog(infos, self)
        dialog.exec()
        self.refresh_requests()

    def _refuse_selected(self) -> None:
        demande = self._selected_demande()
        if demande is None:
            QMessageBox.information(self, "Demandes", "Sélectionnez d'abord une demande.")
            return
        reply = QMessageBox.question(
            self,
            "Refuser la demande",
            f"Refuser la demande de « {demande.nom_complet} » ?\n\n"
            "Le demandeur ne recevra aucun compte : à vous de lui répondre.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        service = self._ensure_service()
        try:
            service.refuse_request(demande.id)
        except PortalError as exc:
            QMessageBox.warning(self, "Demandes", str(exc))
        self.refresh_requests()
