"""Module Export — export d'un lot de comptes en CSV ou en étiquettes PDF.

Flux : choisir une OU (avec ou sans ses sous-OU) → charger les comptes →
choisir les champs à inclure → exporter en CSV, ou en étiquettes PDF
imprimables (formats de planches A4 standards du commerce)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.export import (
    EXPORT_FIELDS,
    LABEL_COLOR_THEMES,
    LABEL_FORMATS,
    build_export_row,
    export_users_csv,
    generate_labels_pdf,
)
from edusync_ad.core.password_vault import PasswordVault

COL_SAM, COL_CN, COL_CLASSE, COL_ETAT = range(4)
PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "Classe / OU", "État"]

DEFAULT_CHECKED_FIELDS = {"identifiant", "nom_complet", "classe"}


class ExportPage(QWidget):
    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        password_vault: PasswordVault,
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.password_vault = password_vault
        self.session_id = session_id

        self._loaded_users: list[dict] = []  # dicts bruts list_users_in_ou/list_ou_contents
        self._field_checkboxes: dict[str, QCheckBox] = {}
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        source_group = QGroupBox("1. Source")
        source_form = QHBoxLayout(source_group)
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(320)
        self.refresh_ous_btn = QPushButton("Actualiser")
        self.refresh_ous_btn.clicked.connect(self._load_ous)
        self.include_sub_ous = QCheckBox("Inclure les sous-OU")
        self.include_sub_ous.setChecked(True)
        self.load_btn = QPushButton("2. Charger les comptes")
        self.load_btn.clicked.connect(self._on_load_clicked)
        source_form.addWidget(QLabel("OU :"))
        source_form.addWidget(self.ou_combo)
        source_form.addWidget(self.refresh_ous_btn)
        source_form.addWidget(self.include_sub_ous)
        source_form.addWidget(self.load_btn)
        source_form.addStretch()

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.load_info = QLabel("")

        fields_group = QGroupBox("3. Champs à inclure")
        fields_outer_layout = QVBoxLayout(fields_group)
        fields_layout = QHBoxLayout()
        for key, label in EXPORT_FIELDS.items():
            chk = QCheckBox(label)
            chk.setChecked(key in DEFAULT_CHECKED_FIELDS)
            self._field_checkboxes[key] = chk
            fields_layout.addWidget(chk)
        fields_layout.addStretch()
        fields_outer_layout.addLayout(fields_layout)

        self.pwd_warning_label = QLabel(
            "⚠ Le mot de passe sera visible en clair dans le fichier exporté — à distribuer avec précaution. "
            "Seuls les mots de passe positionnés par EduSync AD (création, réinitialisation) sont disponibles."
        )
        self.pwd_warning_label.setWordWrap(True)
        self.pwd_warning_label.setStyleSheet("color: #b23a2e; font-weight: 600;")
        self.pwd_warning_label.setVisible("mot_de_passe" in DEFAULT_CHECKED_FIELDS)
        self._field_checkboxes["mot_de_passe"].toggled.connect(self.pwd_warning_label.setVisible)
        fields_outer_layout.addWidget(self.pwd_warning_label)

        format_group = QGroupBox("4. Format d'export")
        format_layout = QVBoxLayout(format_group)
        type_row = QHBoxLayout()
        self.radio_csv = QRadioButton("CSV")
        self.radio_labels = QRadioButton("Étiquettes PDF (imprimable)")
        self.radio_csv.setChecked(True)
        self._type_group = QButtonGroup(self)
        self._type_group.addButton(self.radio_csv)
        self._type_group.addButton(self.radio_labels)
        type_row.addWidget(self.radio_csv)
        type_row.addWidget(self.radio_labels)
        type_row.addStretch()
        format_layout.addLayout(type_row)

        self.label_format_row = QWidget()
        label_format_layout = QHBoxLayout(self.label_format_row)
        label_format_layout.setContentsMargins(24, 0, 0, 0)
        label_format_layout.addWidget(QLabel("Format de planche :"))
        self.label_format_combo = QComboBox()
        for key, fmt in LABEL_FORMATS.items():
            self.label_format_combo.addItem(fmt.nom, key)
        label_format_layout.addWidget(self.label_format_combo)
        label_format_layout.addWidget(QLabel("Couleur :"))
        self.label_color_combo = QComboBox()
        for key, theme in LABEL_COLOR_THEMES.items():
            self.label_color_combo.addItem(theme.nom, key)
        label_format_layout.addWidget(self.label_color_combo)
        self.chk_qr_code = QCheckBox("QR code (identifiant)")
        label_format_layout.addWidget(self.chk_qr_code)
        label_format_layout.addStretch()
        self.label_format_row.setVisible(False)
        format_layout.addWidget(self.label_format_row)

        self.radio_labels.toggled.connect(self.label_format_row.setVisible)

        self.export_btn = QPushButton("Exporter…")
        self.export_btn.clicked.connect(self._on_export_clicked)
        self.export_btn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(source_group)
        row = QHBoxLayout()
        row.addWidget(self.load_info)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self.preview_table)
        layout.addWidget(fields_group)
        layout.addWidget(format_group)
        layout.addWidget(self.export_btn)

    # -- Source ------------------------------------------------------------------

    def _load_ous(self) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))
            return
        previous = self.ou_combo.currentData()
        self.ou_combo.clear()
        self.ou_combo.addItem(f"(racine du domaine) {self.ad_connection.domain}", base_dn)
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.ou_combo.findData(previous)
        self.ou_combo.setCurrentIndex(idx if idx >= 0 else 0)

    # -- Chargement ----------------------------------------------------------------

    def _on_load_clicked(self) -> None:
        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Aucune OU", "Sélectionnez une OU ou actualisez la liste.")
            return
        try:
            if self.include_sub_ous.isChecked():
                users = self.ad_connection.list_users_in_ou(ou_dn)
            else:
                users = [
                    u for u in self.ad_connection.list_ou_contents(ou_dn)
                    if u.get("kind", "user") == "user"
                ]
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        self._loaded_users = users
        self._populate_preview()
        self.export_btn.setEnabled(bool(users))
        self.load_info.setText(f"{len(users)} compte(s) chargé(s).")

    def _populate_preview(self) -> None:
        self.preview_table.setRowCount(len(self._loaded_users))
        for i, u in enumerate(self._loaded_users):
            row = build_export_row({**u, "sAMAccountName": u.get("sam", "")})
            self.preview_table.setItem(i, COL_SAM, QTableWidgetItem(row["identifiant"]))
            self.preview_table.setItem(i, COL_CN, QTableWidgetItem(row["nom_complet"]))
            self.preview_table.setItem(i, COL_CLASSE, QTableWidgetItem(row["classe"]))
            self.preview_table.setItem(i, COL_ETAT, QTableWidgetItem(row["etat"]))

    # -- Export ----------------------------------------------------------------

    def _selected_fields(self) -> list[str]:
        return [key for key, chk in self._field_checkboxes.items() if chk.isChecked()]

    def _on_export_clicked(self) -> None:
        if not self._loaded_users:
            return
        fields = self._selected_fields()
        if not fields:
            QMessageBox.warning(self, "Aucun champ", "Cochez au moins un champ à inclure.")
            return

        # Les listes de chargement (list_users_in_ou / list_ou_contents) ne
        # renvoient que sam/cn/état — les autres champs (mail, prénom, nom…)
        # nécessitent une lecture complète par compte, faite ici une seule
        # fois au moment de l'export plutôt qu'à chaque prévisualisation.
        needs_full_attrs = bool(set(fields) - {"identifiant", "nom_complet", "classe", "etat"})
        want_password = "mot_de_passe" in fields
        rows = []
        if needs_full_attrs:
            for u in self._loaded_users:
                try:
                    attrs = self.ad_connection.get_user_attributes(u["dn"])
                except ADError:
                    attrs = {**u, "sAMAccountName": u.get("sam", "")}
                row = build_export_row(attrs)
                if want_password:
                    # Uniquement les mots de passe qu'EduSync AD a lui-même
                    # positionnés (voir core/password_vault.py) — vide sinon,
                    # jamais d'erreur bloquante pour un compte non enregistré.
                    row["mot_de_passe"] = self.password_vault.get(u.get("sam", "")) or ""
                rows.append(row)
        else:
            rows = [build_export_row({**u, "sAMAccountName": u.get("sam", "")}) for u in self._loaded_users]

        simulation_label = ""  # l'export est une lecture seule, pas concerné par le mode simulation
        if self.radio_csv.isChecked():
            path_str, _ = QFileDialog.getSaveFileName(self, "Exporter en CSV", "export.csv", "CSV (*.csv)")
            if not path_str:
                return
            try:
                export_users_csv(Path(path_str), rows, fields)
            except OSError as exc:
                QMessageBox.critical(self, "Erreur d'export", str(exc))
                return
            self.audit_log.record(
                "export_csv", f"{len(rows)} compte(s)", "succes", self.session_id,
                detail=f"champs={','.join(fields)}",
            )
            QMessageBox.information(self, "Export terminé", f"Fichier enregistré : {path_str}{simulation_label}")
        else:
            path_str, _ = QFileDialog.getSaveFileName(
                self, "Exporter les étiquettes", "etiquettes.pdf", "PDF (*.pdf)"
            )
            if not path_str:
                return
            format_key = self.label_format_combo.currentData()
            color_theme = self.label_color_combo.currentData()
            qr_code = self.chk_qr_code.isChecked()
            try:
                generate_labels_pdf(
                    Path(path_str), rows, fields, format_key,
                    color_theme=color_theme, qr_code=qr_code,
                )
            except OSError as exc:
                QMessageBox.critical(self, "Erreur d'export", str(exc))
                return
            self.audit_log.record(
                "export_etiquettes", f"{len(rows)} compte(s)", "succes", self.session_id,
                detail=f"champs={','.join(fields)}, format={format_key}, couleur={color_theme}, qr={qr_code}",
            )
            QMessageBox.information(self, "Export terminé", f"Fichier enregistré : {path_str}")
