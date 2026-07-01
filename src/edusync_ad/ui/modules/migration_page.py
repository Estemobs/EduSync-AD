"""Module 2 — Migration d'utilisateurs (§5 du cahier des charges).

Import CSV (identifiant, OU source, OU destination) -> résolution dans l'AD
-> prévisualisation -> validation (déplacement + mise à jour des groupes de
classe) -> journal d'actions.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.models import MigrationRow

MIGRATION_COLUMNS = ["identifiant", "ou_source", "ou_destination"]
PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "OU source", "OU destination", "État"]
COL_ID, COL_NOM, COL_SRC, COL_DST, COL_ETAT = range(5)

_ETAT_EN_ATTENTE = "En attente de résolution"
_ETAT_TROUVE = "Trouvé"
_ETAT_NON_TROUVE = "⚠ Non trouvé"
_ETAT_MIGRE = "Migré"
_ETAT_SIMULE = "Simulé"


def _ou_leaf_name(ou_dn: str) -> str:
    leaf = ou_dn.split(",")[0].strip()
    if "=" in leaf:
        return leaf.split("=", 1)[1]
    return leaf


def _load_migration_csv(path: Path) -> tuple[list[str], list[list[str]], str, str]:
    """Retourne (headers, data_rows, delimiter, encoding)."""
    encodings = ["utf-8-sig", "utf-8", "latin-1"]
    for encoding in encodings:
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Impossible de décoder le fichier CSV.")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return [], [], delimiter, encoding
    headers = [h.strip().lower() for h in rows[0]]
    data_rows = rows[1:]
    return headers, data_rows, delimiter, encoding


class MigrationPage(QWidget):
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

        self._csv_path: Path | None = None
        self._csv_headers: list[str] = []
        self._csv_data_rows: list[list[str]] = []
        self._csv_delimiter: str = ";"
        self._mapping_combos: dict[str, QComboBox] = {}
        self._rows: list[MigrationRow] = []

        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        # Onglets : Via CSV / Via l'interface
        self.mode_tabs = QTabWidget()

        # -- Onglet CSV -------------------------------------------------------
        csv_tab = QWidget()
        csv_layout = QVBoxLayout(csv_tab)
        import_group = QGroupBox("Import du fichier de migration CSV")
        import_layout = QVBoxLayout(import_group)

        import_row = QHBoxLayout()
        self.import_button = QPushButton("Choisir un fichier CSV…")
        self.import_button.clicked.connect(self._on_import_clicked)
        self.import_label = QLabel("Aucun fichier importé.")
        import_row.addWidget(self.import_button)
        import_row.addWidget(self.import_label)
        import_row.addStretch()
        import_layout.addLayout(import_row)

        self.mapping_form = QFormLayout()
        mapping_widget = QWidget()
        mapping_widget.setLayout(self.mapping_form)
        import_layout.addWidget(mapping_widget)
        csv_layout.addWidget(import_group)

        csv_resolve_row = QHBoxLayout()
        self.resolve_button = QPushButton("Résoudre dans l'AD")
        self.resolve_button.clicked.connect(self._on_resolve_clicked)
        csv_resolve_row.addWidget(self.resolve_button)
        csv_resolve_row.addStretch()
        csv_layout.addLayout(csv_resolve_row)
        self.mode_tabs.addTab(csv_tab, "Via CSV")

        # -- Onglet Interface -------------------------------------------------
        iface_tab = QWidget()
        iface_layout = QVBoxLayout(iface_tab)
        iface_group = QGroupBox("Sélection directe des OUs")
        iface_form = QFormLayout(iface_group)
        self.iface_ou_src = QLineEdit()
        self.iface_ou_src.setPlaceholderText("OU=4emeA,OU=eleves,DC=lycee,DC=local")
        self.iface_ou_dst = QLineEdit()
        self.iface_ou_dst.setPlaceholderText("OU=3emeA,OU=eleves,DC=lycee,DC=local")
        iface_form.addRow("OU source :", self.iface_ou_src)
        iface_form.addRow("OU destination :", self.iface_ou_dst)
        iface_layout.addWidget(iface_group)

        iface_load_row = QHBoxLayout()
        self.iface_load_button = QPushButton("Charger les utilisateurs de l'OU source")
        self.iface_load_button.clicked.connect(self._on_iface_load_clicked)
        iface_load_row.addWidget(self.iface_load_button)
        iface_load_row.addStretch()
        iface_layout.addLayout(iface_load_row)
        iface_layout.addStretch()
        self.mode_tabs.addTab(iface_tab, "Via l'interface")

        resolve_row = QHBoxLayout()
        self.info_label = QLabel("")
        resolve_row.addWidget(self.info_label)
        resolve_row.addStretch()

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        action_row = QHBoxLayout()
        self.validate_button = QPushButton("Valider la migration")
        self.validate_button.clicked.connect(self._on_validate_clicked)
        self.validate_button.setEnabled(False)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self.mode_tabs)
        layout.addLayout(resolve_row)
        layout.addWidget(self.preview_table)
        layout.addLayout(action_row)

    # -- Import CSV et mapping -------------------------------------------------

    def _on_import_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Importer le fichier de migration", "", "CSV (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        try:
            headers, data_rows, delimiter, encoding = _load_migration_csv(path)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Erreur d'import", str(exc))
            return

        self._csv_path = path
        self._csv_headers = headers
        self._csv_data_rows = data_rows
        self._csv_delimiter = delimiter
        self.import_label.setText(f"{path.name} — {len(data_rows)} ligne(s)")
        self._rebuild_mapping_form(headers)
        self._rows = []
        self.preview_table.setRowCount(0)
        self.validate_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.info_label.setText("")

    def _rebuild_mapping_form(self, headers: list[str]) -> None:
        while self.mapping_form.rowCount():
            self.mapping_form.removeRow(0)
        self._mapping_combos.clear()

        for column in MIGRATION_COLUMNS:
            combo = QComboBox()
            combo.addItem("(non utilisé)", "")
            for header in headers:
                combo.addItem(header, header)
            suggested = column if column in headers else ""
            if suggested:
                combo.setCurrentIndex(combo.findData(suggested))
            label = column + " *"
            self.mapping_form.addRow(label, combo)
            self._mapping_combos[column] = combo

    def _current_mapping(self) -> dict[str, str]:
        return {col: combo.currentData() or "" for col, combo in self._mapping_combos.items()}

    # -- Résolution dans l'AD -------------------------------------------------

    def _on_resolve_clicked(self) -> None:
        if self._csv_path is None:
            QMessageBox.warning(self, "Aucun fichier", "Importez d'abord un fichier CSV.")
            return
        mapping = self._current_mapping()
        missing = [c for c in MIGRATION_COLUMNS if not mapping.get(c)]
        if missing:
            QMessageBox.warning(
                self,
                "Colonnes manquantes",
                f"Associez les colonnes obligatoires : {', '.join(missing)}",
            )
            return
        if self.ad_connection.domain is None:
            QMessageBox.critical(self, "Non connecté", "Aucune connexion à l'Active Directory.")
            return

        idx_id = self._csv_headers.index(mapping["identifiant"])
        idx_src = self._csv_headers.index(mapping["ou_source"])
        idx_dst = self._csv_headers.index(mapping["ou_destination"])

        rows: list[MigrationRow] = []
        for data_row in self._csv_data_rows:
            if len(data_row) <= max(idx_id, idx_src, idx_dst):
                continue
            identifiant = data_row[idx_id].strip()
            ou_src = data_row[idx_src].strip()
            ou_dst = data_row[idx_dst].strip()
            if not identifiant or not ou_src or not ou_dst:
                continue
            rows.append(MigrationRow(identifiant=identifiant, ou_source=ou_src, ou_destination=ou_dst))

        if not rows:
            QMessageBox.warning(self, "Aucune ligne valide", "Le fichier ne contient aucune ligne exploitable.")
            return

        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        not_found_count = 0

        for row in rows:
            try:
                result = self.ad_connection.search_user_by_sam(row.identifiant, base_dn)
            except ADError as exc:
                row.erreur = str(exc)
                continue
            if result is None:
                not_found_count += 1
            else:
                row.user_dn, row.nom_complet = result

        self._rows = rows
        self._populate_table()
        self.validate_button.setEnabled(bool(rows))
        self.cancel_button.setEnabled(True)

        if not_found_count:
            self.info_label.setText(
                f"⚠ {not_found_count} utilisateur(s) non trouvé(s) — la migration continuera pour les autres."
            )
        else:
            self.info_label.setText(f"{len(rows)} utilisateur(s) résolu(s).")

    # -- Prévisualisation ------------------------------------------------------

    def _populate_table(self) -> None:
        self.preview_table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self._set_table_row(i, row)

    def _set_table_row(self, i: int, row: MigrationRow, etat: str | None = None) -> None:
        self.preview_table.setItem(i, COL_ID, QTableWidgetItem(row.identifiant))
        self.preview_table.setItem(i, COL_NOM, QTableWidgetItem(row.nom_complet or ""))
        self.preview_table.setItem(i, COL_SRC, QTableWidgetItem(row.ou_source))
        self.preview_table.setItem(i, COL_DST, QTableWidgetItem(row.ou_destination))

        if etat is None:
            if row.erreur:
                etat = f"Erreur : {row.erreur}"
            elif row.user_dn is None:
                etat = _ETAT_NON_TROUVE
            else:
                etat = _ETAT_TROUVE
        self.preview_table.setItem(i, COL_ETAT, QTableWidgetItem(etat))

    # -- Validation / déplacement ---------------------------------------------

    def _on_validate_clicked(self) -> None:
        to_migrate = [r for r in self._rows if r.user_dn is not None and not r.erreur]
        skipped = [r for r in self._rows if r.user_dn is None and not r.erreur]

        if not to_migrate:
            QMessageBox.warning(self, "Rien à migrer", "Aucun utilisateur résolu à déplacer.")
            return

        simulation = self.ad_connection.dry_run
        suffix_sim = " (mode simulation)" if simulation else ""

        msg = f"{len(to_migrate)} utilisateur(s) vont être déplacés{suffix_sim}."
        if skipped:
            msg += f"\n{len(skipped)} utilisateur(s) non trouvé(s) seront ignorés."
        reply = QMessageBox.question(
            self,
            "Confirmer la migration",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success_count = 0
        for i, row in enumerate(self._rows):
            if row.user_dn is None:
                continue
            try:
                self._migrate_one(row)
            except ADError as exc:
                row.erreur = str(exc)
                self.audit_log.record(
                    "migration_compte",
                    row.identifiant,
                    "echec",
                    self.session_id,
                    ou_source=row.ou_source,
                    ou_destination=row.ou_destination,
                    simulation=simulation,
                    detail=str(exc),
                )
                self._set_table_row(i, row)
            else:
                success_count += 1
                self.audit_log.record(
                    "migration_compte",
                    row.identifiant,
                    "succes",
                    self.session_id,
                    ou_source=row.ou_source,
                    ou_destination=row.ou_destination,
                    simulation=simulation,
                )
                etat = _ETAT_SIMULE if simulation else _ETAT_MIGRE
                self._set_table_row(i, row, etat=etat)

        QMessageBox.information(
            self,
            "Migration terminée",
            f"{success_count}/{len(to_migrate)} compte(s) migré(s){suffix_sim}.",
        )

    def _migrate_one(self, row: MigrationRow) -> None:
        assert row.user_dn is not None
        self.ad_connection.move_user(row.user_dn, row.ou_destination)

        if not self.config.groupes_classe_auto:
            return

        src_leaf = _ou_leaf_name(row.ou_source)
        dst_leaf = _ou_leaf_name(row.ou_destination)
        src_group_dn = f"CN={src_leaf},{row.ou_source}"
        dst_group_dn = f"CN={dst_leaf},{row.ou_destination}"

        new_user_dn = row.user_dn.split(",")[0] + "," + row.ou_destination

        try:
            if self.ad_connection.group_exists(src_group_dn):
                self.ad_connection.remove_user_from_group(row.user_dn, src_group_dn)
        except ADError:
            pass

        if not self.ad_connection.group_exists(dst_group_dn):
            self.ad_connection.create_group(dst_group_dn, dst_leaf)
        self.ad_connection.add_user_to_group(new_user_dn, dst_group_dn)

    # -- Annulation -----------------------------------------------------------

    def _on_cancel_clicked(self) -> None:
        self._rows = []
        self.preview_table.setRowCount(0)
        self.validate_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.info_label.setText("")
