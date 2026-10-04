"""Gestion des sites gérés (M25 — multisite / multi-domaine).

Liste des profils de domaine + formulaire de création/édition. Les profils
sont persistés chiffrés dans ``domaines.json`` (voir ``core/multisite``).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from edusync_ad.core.multisite import (
    DomainProfile,
    ensure_sites,
    save_sites,
    unique_site_id,
)


class DomainsDialog(QDialog):
    """Ajouter, modifier et supprimer les domaines AD gérés par l'application."""

    def __init__(
        self,
        parent=None,
        *,
        profiles: list[DomainProfile] | None = None,
        current_domain: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Domaines gérés")
        self.setMinimumSize(780, 440)

        self._current_domain = current_domain.strip().lower()
        self._editing_id: str | None = None
        self._loading = False
        self.profiles: list[DomainProfile] = (
            list(profiles) if profiles is not None else ensure_sites()
        )

        self._build_ui()
        self._refresh_list()
        if self.profiles:
            self.list_widget.setCurrentRow(0)
        else:
            self._start_new_profile()

    # -- Construction --------------------------------------------------------

    def _build_ui(self) -> None:
        # Liste des sites
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.list_widget.setMinimumWidth(240)

        add_btn = QPushButton("＋ Ajouter un site")
        add_btn.clicked.connect(self._start_new_profile)
        remove_btn = QPushButton("Supprimer")
        remove_btn.clicked.connect(self._on_remove)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(add_btn)
        list_buttons.addWidget(remove_btn)

        left = QVBoxLayout()
        left.addWidget(QLabel("Sites enregistrés"))
        left.addWidget(self.list_widget, stretch=1)
        left.addLayout(list_buttons)

        # Formulaire
        self.label_edit = QLineEdit()
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("lycee-victor-hugo.local")
        self.controller_edit = QLineEdit()
        self.controller_edit.setPlaceholderText("10.0.0.5 ou dc01.lycee-victor-hugo.local")
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_password_check = QCheckBox("Mémoriser ce mot de passe (chiffré sur ce poste)")
        self.verify_cert_check = QCheckBox("Vérifier le certificat du contrôleur en LDAPS")
        self.verify_cert_check.setChecked(True)
        self.ca_edit = QLineEdit()
        self.ca_edit.setPlaceholderText("Certificat racine (.pem/.crt), optionnel")
        ca_browse = QPushButton("Parcourir…")
        ca_browse.clicked.connect(self._on_browse_ca)
        ca_row = QHBoxLayout()
        ca_row.addWidget(self.ca_edit)
        ca_row.addWidget(ca_browse)

        self.active_label = QLabel("")
        self.active_label.setStyleSheet("color: #2fa84f; font-weight: 600;")
        self.domain_edit.textChanged.connect(lambda _text: self._refresh_active_hint())

        form = QFormLayout()
        form.addRow("Libellé", self.label_edit)
        form.addRow("Nom de domaine", self.domain_edit)
        form.addRow("Contrôleur de domaine", self.controller_edit)
        form.addRow("Nom d'utilisateur", self.username_edit)
        form.addRow("Mot de passe", self.password_edit)
        form.addRow("", self.remember_password_check)
        form.addRow("", self.verify_cert_check)
        form.addRow("Certificat CA", ca_row)
        form.addRow("", self.active_label)

        hint = QLabel(
            "Un seul domaine est connecté à la fois : le changement de site referme la "
            "session en cours et rouvre l'écran de connexion avec le profil choisi."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")

        right = QVBoxLayout()
        right.addWidget(QLabel("Fiche du site"))
        right.addLayout(form)
        right.addWidget(hint)
        right.addStretch()

        body = QHBoxLayout()
        body.addLayout(left)
        body.addLayout(right)

        buttons = QDialogButtonBox()
        buttons.addButton("Enregistrer", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Fermer", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(buttons)

    # -- Liste ---------------------------------------------------------------

    def _refresh_list(self) -> None:
        self._loading = True
        try:
            current_id = self._editing_id
            self.list_widget.clear()
            for profile in self.profiles:
                suffix = "  ● connecté" if profile.domain.strip().lower() == self._current_domain else ""
                item = QListWidgetItem(f"{profile.display_name}{suffix}")
                item.setData(Qt.ItemDataRole.UserRole, profile.id)
                self.list_widget.addItem(item)
            row = self._row_for_id(current_id)
            self.list_widget.setCurrentRow(row)
        finally:
            self._loading = False

    def _row_for_id(self, site_id: str | None) -> int:
        if not site_id:
            return -1
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == site_id:
                return row
        return -1

    def _profile_at_row(self, row: int) -> DomainProfile | None:
        if row < 0:
            return None
        site_id = self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        for profile in self.profiles:
            if profile.id == site_id:
                return profile
        return None

    def _on_row_changed(self, row: int) -> None:
        if self._loading:
            return
        profile = self._profile_at_row(row)
        if profile is None:
            return
        self._editing_id = profile.id
        self._fill_form(profile)

    # -- Formulaire ----------------------------------------------------------

    def _fill_form(self, profile: DomainProfile) -> None:
        self.label_edit.setText(profile.label)
        self.domain_edit.setText(profile.domain)
        self.controller_edit.setText(profile.controller)
        self.username_edit.setText(profile.username)
        self.password_edit.setText(profile.password)
        self.remember_password_check.setChecked(profile.remember_password)
        self.verify_cert_check.setChecked(profile.verify_certificate)
        self.ca_edit.setText(profile.ca_cert_path)
        self._refresh_active_hint()

    def _start_new_profile(self) -> None:
        self._editing_id = None
        self._loading = True
        try:
            self.list_widget.clearSelection()
        finally:
            self._loading = False
        self._fill_form(DomainProfile(id="", label="", domain=""))

    def _read_form(self) -> DomainProfile:
        return DomainProfile(
            id=self._editing_id or unique_site_id(self.profiles),
            label=self.label_edit.text(),
            domain=self.domain_edit.text(),
            controller=self.controller_edit.text(),
            username=self.username_edit.text(),
            password=self.password_edit.text(),
            remember_password=self.remember_password_check.isChecked(),
            verify_certificate=self.verify_cert_check.isChecked(),
            ca_cert_path=self.ca_edit.text(),
        )

    def _refresh_active_hint(self) -> None:
        domain = self.domain_edit.text().strip().lower()
        if domain and domain == self._current_domain:
            self.active_label.setText("● Ce domaine est celui de la session en cours")
        else:
            self.active_label.setText("")

    def _on_browse_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Certificat racine de l'AD", "", "Certificats (*.pem *.crt *.cer);;Tous les fichiers (*)"
        )
        if path:
            self.ca_edit.setText(path)

    # -- Actions -------------------------------------------------------------

    def _on_remove(self) -> None:
        profile = self._profile_at_row(self.list_widget.currentRow())
        if profile is None:
            QMessageBox.information(self, "Suppression", "Sélectionnez d'abord un site dans la liste.")
            return
        warning = ""
        if profile.domain.strip().lower() == self._current_domain:
            warning = (
                "\n\nCe domaine est celui de la session en cours : il reste connecté jusqu'à la "
                "prochaine reconnexion, mais ne sera plus proposé dans le sélecteur."
            )
        answer = QMessageBox.question(
            self,
            "Supprimer le site",
            f"Supprimer le site « {profile.display_name} » ?{warning}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        previous = self.profiles
        self.profiles = [item for item in self.profiles if item.id != profile.id]
        if not self._persist():
            self.profiles = previous
            return
        if self._editing_id == profile.id:
            self._editing_id = None
        self._refresh_list()
        if self.profiles:
            self.list_widget.setCurrentRow(0)
        else:
            self._start_new_profile()

    def _on_save(self) -> None:
        profile = self._read_form()
        errors = profile.validate()
        if errors:
            QMessageBox.warning(self, "Site incomplet", "\n".join(f"• {error}" for error in errors))
            return
        cleaned = profile.sanitized()
        replaced = False
        for index, existing in enumerate(self.profiles):
            if existing.id == cleaned.id:
                self.profiles[index] = cleaned
                replaced = True
                break
        if not replaced:
            self.profiles.append(cleaned)
        if not self._persist():
            return
        self._editing_id = cleaned.id
        self._refresh_list()
        self.accept()

    def _persist(self) -> bool:
        try:
            save_sites(self.profiles)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Écriture impossible",
                f"Impossible d'enregistrer domaines.json : {exc}",
            )
            return False
        return True
