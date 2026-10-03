"""Module 17 — Dossiers personnels (Home Directory).

Configuration du partage racine (``\\srv\\homes``), de la lettre de lecteur
(``H:``) et du modèle de dossier (``%USERNAME%`` / ``%SAM%`` / ``%OU%``).
Prévisualisation par OU, application des attributs AD (``homeDirectory`` +
``homeDrive``), génération du script PowerShell de création + droits NTFS
(utilisateur : Modification ; Administrateurs et SYSTEM : Contrôle total).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.homedirs import (
    HomeDirConfig,
    HomeDirManager,
    HomeDirPlan,
    load_home_dir_config,
    save_home_dir_config,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "Dossier personnel", "Lettre", "État"]
COL_SAM, COL_NOM, COL_PATH, COL_DRIVE, COL_ETAT = range(5)


class HomeDirsPage(QWidget):
    """Page M17 — dossiers personnels (attributs AD + droits NTFS)."""

    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.session_id = session_id

        self._cfg: HomeDirConfig = load_home_dir_config()
        self._plans: list[HomeDirPlan] = []
        self._preview_users: list[dict] = []

        self._build_ui()
        self._load_config_into_form()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Configuration
        config_group = QGroupBox("1. Configuration du dossier personnel")
        form = QFormLayout(config_group)

        self.share_edit = QLineEdit()
        self.share_edit.setPlaceholderText(r"ex : \\srv\homes")
        form.addRow("Partage racine :", self.share_edit)

        self.drive_edit = QLineEdit()
        self.drive_edit.setMaximumWidth(80)
        self.drive_edit.setPlaceholderText("H:")
        form.addRow("Lettre de lecteur :", self.drive_edit)

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("ex : %USERNAME% ou %SAM%")
        form.addRow("Modèle de dossier :", self.template_edit)

        self.on_create_check = QCheckBox("Appliquer automatiquement à la création de compte")
        form.addRow("", self.on_create_check)

        hint = QLabel(
            "Variables : %USERNAME% (nom complet), %SAM% (identifiant), "
            "%OU% (nom de l'OU), %DOMAIN% (domaine)"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        form.addRow("", hint)

        save_row = QHBoxLayout()
        save_btn = QPushButton("Enregistrer la configuration")
        save_btn.clicked.connect(self._on_save_config)
        save_row.addWidget(save_btn)
        save_row.addStretch()
        form.addRow("", save_row)

        # 2. Prévisualisation
        preview_group = QGroupBox("2. Prévisualisation (comptes d'une OU)")
        preview_layout = QVBoxLayout(preview_group)

        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("OU :"))
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(340)
        preview_row.addWidget(self.ou_combo)
        load_btn = QPushButton("Charger les comptes")
        load_btn.clicked.connect(self._on_preview_clicked)
        preview_row.addWidget(load_btn)
        preview_row.addStretch()
        preview_layout.addLayout(preview_row)

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        preview_layout.addWidget(self.preview_table)

        # 3. Actions
        actions_row = QHBoxLayout()
        self.apply_btn = QPushButton("3a. Appliquer homeDirectory / homeDrive (AD)")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        actions_row.addWidget(self.apply_btn)

        self.script_btn = QPushButton("3b. Générer le script PowerShell (droits NTFS)")
        self.script_btn.setEnabled(False)
        self.script_btn.clicked.connect(self._on_generate_script)
        actions_row.addWidget(self.script_btn)
        actions_row.addStretch()
        preview_layout.addLayout(actions_row)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(config_group)
        layout.addWidget(preview_group)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

    def _load_config_into_form(self) -> None:
        self.share_edit.setText(self._cfg.share_root)
        self.drive_edit.setText(self._cfg.drive_letter)
        self.template_edit.setText(self._cfg.folder_template)
        self.on_create_check.setChecked(self._cfg.apply_on_create)

    def _load_ous(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        previous = self.ou_combo.currentData()
        self.ou_combo.clear()
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.ou_combo.findData(previous)
        self.ou_combo.setCurrentIndex(idx if idx >= 0 else 0)

    # -- Configuration ----------------------------------------------------------

    def _read_form_config(self) -> HomeDirConfig | None:
        cfg = HomeDirConfig(
            share_root=self.share_edit.text().strip(),
            drive_letter=self.drive_edit.text().strip(),
            folder_template=self.template_edit.text().strip(),
            apply_on_create=self.on_create_check.isChecked(),
        )
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return None
        return cfg

    def _on_save_config(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        self._cfg = cfg
        save_home_dir_config(cfg)
        self.audit_log.record(
            "configuration_dossier_personnel", "-", "succes", self.session_id,
            detail=f"racine={cfg.share_root} lettre={cfg.drive_letter} modele={cfg.folder_template}",
        )
        QMessageBox.information(self, "Enregistré", "Configuration des dossiers personnels enregistrée.")

    # -- Prévisualisation ---------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        self._cfg = cfg

        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        domain = self.ad_connection.domain or ""
        mgr = HomeDirManager(cfg)
        self._preview_users = users
        self._plans = mgr.plan_batch(users, ou_dn, domain)

        self.preview_table.setRowCount(len(self._plans))
        for row, (user, plan) in enumerate(zip(users, self._plans)):
            self.preview_table.setItem(row, COL_SAM, QTableWidgetItem(plan.sam))
            self.preview_table.setItem(row, COL_NOM, QTableWidgetItem(plan.username))
            self.preview_table.setItem(row, COL_PATH, QTableWidgetItem(plan.home_path))
            self.preview_table.setItem(row, COL_DRIVE, QTableWidgetItem(plan.drive_letter))
            item = QTableWidgetItem("Prêt")
            item.setForeground(Qt.GlobalColor.darkGreen)
            self.preview_table.setItem(row, COL_ETAT, item)

        self.apply_btn.setEnabled(bool(self._plans))
        self.script_btn.setEnabled(bool(self._plans))

    # -- Application AD ----------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        if not self._plans:
            return
        plans = list(self._plans)
        ou_dn = self.ou_combo.currentData() or ""
        labels = [f"{p.sam} → {p.home_path}" for p in plans]
        self.apply_btn.setEnabled(False)

        def run_one(plan: HomeDirPlan) -> None:
            for attr, value in plan.ad_attributes.items():
                self.ad_connection.update_user_attribute(plan.user_dn, attr, value)

        def on_result(position: int, success: bool, message: str) -> None:
            plan = plans[position]
            if success:
                self.audit_log.record(
                    "application_dossier_personnel", plan.sam, "succes", self.session_id,
                    ou_source=ou_dn, detail=f"{plan.home_path} ({plan.drive_letter})",
                )
            else:
                self.audit_log.record(
                    "application_dossier_personnel", plan.sam, "echec", self.session_id,
                    ou_source=ou_dn, detail=message,
                )
            item = self.preview_table.item(position, COL_ETAT)
            if item:
                item.setText("✓" if success else f"✗ {message}")
                item.setForeground(
                    Qt.GlobalColor.darkGreen if success else Qt.GlobalColor.red
                )

        def on_finished() -> None:
            self.apply_btn.setEnabled(True)
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(plans)} dossier(s) défini(s) dans l'AD.",
            )

        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Définition des dossiers personnels en cours…",
            plans, labels, run_one, on_item_result=on_result,
        )

    # -- Script PowerShell ---------------------------------------------------------

    def _on_generate_script(self) -> None:
        if not self._plans:
            return
        default_name = "homedirs_m17.ps1"
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le script PowerShell", default_name,
            "Scripts PowerShell (*.ps1);;Tous les fichiers (*)",
        )
        if not dest:
            return
        mgr = HomeDirManager(self._cfg)
        domain = self.ad_connection.domain or ""
        try:
            path = mgr.save_powershell_script(self._plans, Path(dest), domain=domain)
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Impossible d'écrire le script : {exc}")
            return
        self.audit_log.record(
            "generation_script_homedirs", "-", "succes", self.session_id,
            detail=f"{len(self._plans)} compte(s) → {path}",
        )
        QMessageBox.information(
            self, "Script généré",
            f"{len(self._plans)} dossier(s) dans « {path} ».\n\n"
            "Exécutez-le en admin sur le serveur de fichiers pour créer les "
            "dossiers et appliquer les droits NTFS "
            "(utilisateur : Modification ; Administrateurs et SYSTEM : Contrôle total).",
        )
