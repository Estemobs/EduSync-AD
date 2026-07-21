"""Module 3 — Gestion des départs (§6 du cahier des charges).

Import CSV (identifiant) -> choix du mode (désactivation immédiate ou
suppression différée) -> résolution dans l'AD -> prévisualisation ->
validation -> journal d'actions.

Mode désactivation immédiate : retire l'utilisateur de tous ses groupes,
puis désactive le compte (userAccountControl = 514).

Mode suppression différée : retire des groupes, déplace vers l'OU d'archivage,
enregistre dans la file d'attente (pending_deletions). Un panneau dédié liste
les comptes dont le délai est écoulé et permet de les supprimer définitivement.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.models import DepartRow
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.ui.progress_panel import BatchProgressPanel

DEPART_COLUMNS = ["identifiant"]
PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "Groupes", "État"]
COL_ID, COL_NOM, COL_GROUPES, COL_ETAT = range(4)

MODE_DESACTIVATION = "desactivation"
MODE_ARCHIVAGE = "archivage"


def _load_depart_csv(path: Path) -> tuple[list[str], list[list[str]], str]:
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
        return [], [], delimiter
    headers = [h.strip().lower() for h in rows[0]]
    return headers, rows[1:], delimiter


class DepartPage(QWidget):
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

        self._csv_path: Path | None = None
        self._csv_headers: list[str] = []
        self._csv_data_rows: list[list[str]] = []
        self._mapping_combos: dict[str, QComboBox] = {}
        self._rows: list[DepartRow] = []

        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config
        self.delai_spin.setValue(config.delai_suppression_jours)
        self._refresh_pending_panel()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_pending_panel()

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        import_group = QGroupBox("1. Import du fichier CSV (identifiants des partants)")
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

        mode_group = QGroupBox("2. Mode d'action")
        mode_layout = QVBoxLayout(mode_group)
        self.radio_desactivation = QRadioButton("Désactivation immédiate (retirer des groupes + désactiver le compte)")
        self.radio_desactivation.setChecked(True)
        self.radio_archivage = QRadioButton("Suppression différée (retirer des groupes + déplacer vers OU d'archivage)")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.radio_desactivation)
        self._mode_group.addButton(self.radio_archivage)
        mode_layout.addWidget(self.radio_desactivation)
        mode_layout.addWidget(self.radio_archivage)

        self.delai_widget = QWidget()
        delai_row = QHBoxLayout(self.delai_widget)
        delai_row.setContentsMargins(24, 0, 0, 0)
        delai_row.addWidget(QLabel("Supprimer définitivement après :"))
        self.delai_spin = QSpinBox()
        self.delai_spin.setRange(1, 3650)
        self.delai_spin.setSuffix(" jour(s)")
        self.delai_spin.setValue(self.config.delai_suppression_jours)
        delai_row.addWidget(self.delai_spin)
        delai_row.addStretch()
        self.delai_widget.setVisible(False)
        mode_layout.addWidget(self.delai_widget)

        self.radio_archivage.toggled.connect(self.delai_widget.setVisible)

        resolve_row = QHBoxLayout()
        self.resolve_button = QPushButton("3. Résoudre dans l'AD")
        self.resolve_button.clicked.connect(self._on_resolve_clicked)
        resolve_row.addWidget(self.resolve_button)
        self.info_label = QLabel("")
        resolve_row.addWidget(self.info_label)
        resolve_row.addStretch()

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        action_row = QHBoxLayout()
        self.validate_button = QPushButton("Valider")
        self.validate_button.clicked.connect(self._on_validate_clicked)
        self.validate_button.setEnabled(False)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch()

        # Panneau suppressions en attente
        self.pending_group = QGroupBox("Suppressions en attente")
        pending_layout = QVBoxLayout(self.pending_group)

        pending_info_row = QHBoxLayout()
        self.pending_label = QLabel("")
        self.process_pending_button = QPushButton("Supprimer les comptes échus")
        self.process_pending_button.clicked.connect(self._on_process_pending_clicked)
        pending_info_row.addWidget(self.pending_label)
        pending_info_row.addStretch()
        pending_info_row.addWidget(self.process_pending_button)
        pending_layout.addLayout(pending_info_row)

        self.pending_table = QTableWidget(0, 4)
        self.pending_table.setHorizontalHeaderLabels(["Identifiant", "Nom complet", "Archivé le", "Échéance"])
        self.pending_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.pending_table.horizontalHeader().setStretchLastSection(True)
        self.pending_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pending_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pending_table.setMaximumHeight(160)
        pending_layout.addWidget(self.pending_table)

        cancel_pending_row = QHBoxLayout()
        self.cancel_pending_button = QPushButton("Annuler la suppression programmée")
        self.cancel_pending_button.clicked.connect(self._on_cancel_pending_clicked)
        self.cancel_pending_button.setEnabled(False)
        self.pending_table.itemSelectionChanged.connect(self._on_pending_selection_changed)
        cancel_pending_row.addStretch()
        cancel_pending_row.addWidget(self.cancel_pending_button)
        pending_layout.addLayout(cancel_pending_row)

        self.progress_panel = BatchProgressPanel()
        self.pending_progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(import_group)
        layout.addWidget(mode_group)
        layout.addLayout(resolve_row)
        layout.addWidget(self.preview_table)
        layout.addLayout(action_row)
        layout.addWidget(self.progress_panel)
        layout.addWidget(self.pending_group)
        layout.addWidget(self.pending_progress_panel)

        self._refresh_pending_panel()

    # -- Import CSV -----------------------------------------------------------

    def _on_import_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Importer le fichier des départs", "", "CSV (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        try:
            headers, data_rows, delimiter = _load_depart_csv(path)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Erreur d'import", str(exc))
            return

        self._csv_path = path
        self._csv_headers = headers
        self._csv_data_rows = data_rows
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

        # Le personnel administratif ne connaît jamais l'identifiant AD d'un
        # élève — seulement son prénom et son nom. On accepte donc soit
        # "identifiant" (recherche exacte si disponible), soit "prenom" +
        # "nom" (recherche par nom dans tout l'annuaire), au choix.
        for column in ("identifiant", "prenom", "nom"):
            combo = QComboBox()
            combo.addItem("(non utilisé)", "")
            for header in headers:
                combo.addItem(header, header)
            if column in headers:
                combo.setCurrentIndex(combo.findData(column))
            self._mapping_combos[column] = combo
            self.mapping_form.addRow(column, combo)

    # -- Résolution -----------------------------------------------------------

    def _on_resolve_clicked(self) -> None:
        if self._csv_path is None:
            QMessageBox.warning(self, "Aucun fichier", "Importez d'abord un fichier CSV.")
            return

        col_id = self._mapping_combos["identifiant"].currentData()
        col_prenom = self._mapping_combos["prenom"].currentData()
        col_nom = self._mapping_combos["nom"].currentData()
        if not col_id and not (col_prenom and col_nom):
            QMessageBox.warning(
                self, "Colonnes manquantes",
                "Associez soit la colonne 'identifiant', soit les colonnes 'prenom' et 'nom'.",
            )
            return
        if self.ad_connection.domain is None:
            QMessageBox.critical(self, "Non connecté", "Aucune connexion à l'Active Directory.")
            return

        idx_id = self._csv_headers.index(col_id) if col_id else None
        idx_prenom = self._csv_headers.index(col_prenom) if col_prenom else None
        idx_nom = self._csv_headers.index(col_nom) if col_nom else None
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)

        # Chaque ligne est résolue soit par identifiant direct, soit par
        # recherche prénom+nom dans tout l'annuaire (cas le plus courant pour
        # une liste fournie par le personnel administratif, qui ne connaît
        # jamais l'identifiant AD d'un élève).
        rows: list[DepartRow] = []
        by_sam_idx: list[int] = []
        by_name_idx: list[tuple[int, str]] = []
        for data_row in self._csv_data_rows:
            if idx_id is not None and len(data_row) > idx_id and data_row[idx_id].strip():
                rows.append(DepartRow(identifiant=data_row[idx_id].strip()))
                by_sam_idx.append(len(rows) - 1)
            elif idx_prenom is not None and idx_nom is not None and len(data_row) > max(idx_prenom, idx_nom):
                prenom, nom = data_row[idx_prenom].strip(), data_row[idx_nom].strip()
                if prenom and nom:
                    rows.append(DepartRow(identifiant=""))
                    by_name_idx.append((len(rows) - 1, f"{prenom} {nom}"))

        if not rows:
            QMessageBox.warning(self, "Aucune ligne valide", "Le fichier ne contient aucune ligne exploitable.")
            return

        not_found = 0

        for row_index in by_sam_idx:
            row = rows[row_index]
            try:
                result = self.ad_connection.search_user_by_sam(row.identifiant, base_dn)
            except ADError as exc:
                row.erreur = str(exc)
                continue
            if result is None:
                not_found += 1
                continue
            row.user_dn, row.nom_complet = result

        for row_index, full_name in by_name_idx:
            row = rows[row_index]
            try:
                user_dn = self.ad_connection.search_user_by_cn(full_name, base_dn)
            except ADError as exc:
                row.erreur = str(exc)
                continue
            if user_dn is None:
                not_found += 1
                continue
            try:
                attrs = self.ad_connection.get_user_attributes(user_dn)
            except ADError as exc:
                row.erreur = str(exc)
                continue
            row.identifiant = attrs.get("sAMAccountName", full_name)
            row.user_dn = user_dn
            row.nom_complet = attrs.get("cn", full_name)

        for row in rows:
            if not row.user_dn or row.erreur:
                continue
            try:
                row.groupe_dns = self.ad_connection.search_user_groups(row.user_dn, base_dn)
            except ADError:
                row.groupe_dns = []

        self._rows = rows
        self._populate_table()
        self.validate_button.setEnabled(bool(rows))
        self.cancel_button.setEnabled(True)

        if not_found:
            self.info_label.setText(f"⚠ {not_found} utilisateur(s) non trouvé(s).")
        else:
            self.info_label.setText(f"{len(rows)} utilisateur(s) résolu(s).")

    # -- Prévisualisation -----------------------------------------------------

    def _populate_table(self) -> None:
        self.preview_table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self._set_table_row(i, row)

    def _set_table_row(self, i: int, row: DepartRow, etat: str | None = None) -> None:
        self.preview_table.setItem(i, COL_ID, QTableWidgetItem(row.identifiant))
        self.preview_table.setItem(i, COL_NOM, QTableWidgetItem(row.nom_complet or ""))
        groupes_str = ", ".join(g.split(",")[0].split("=", 1)[-1] for g in row.groupe_dns)
        self.preview_table.setItem(i, COL_GROUPES, QTableWidgetItem(groupes_str))

        if etat is None:
            if row.erreur:
                etat = f"Erreur : {row.erreur}"
            elif row.user_dn is None:
                etat = "⚠ Non trouvé"
            else:
                etat = "Trouvé"
        self.preview_table.setItem(i, COL_ETAT, QTableWidgetItem(etat))

    # -- Validation -----------------------------------------------------------

    def _current_mode(self) -> str:
        return MODE_DESACTIVATION if self.radio_desactivation.isChecked() else MODE_ARCHIVAGE

    def _on_validate_clicked(self) -> None:
        to_process = [r for r in self._rows if r.user_dn is not None and not r.erreur]
        if not to_process:
            QMessageBox.warning(self, "Rien à traiter", "Aucun utilisateur résolu à traiter.")
            return

        mode = self._current_mode()

        if mode == MODE_ARCHIVAGE and not self.config.ou_archive:
            QMessageBox.critical(
                self, "OU d'archivage manquante",
                "Configurez l'OU d'archivage dans les Paramètres avant de continuer."
            )
            return

        mode_label = "désactivés" if mode == MODE_DESACTIVATION else "archivés (suppression différée)"
        reply = QMessageBox.question(
            self,
            "Confirmer",
            f"{len(to_process)} compte(s) vont être {mode_label}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        action_type = "desactivation_compte" if mode == MODE_DESACTIVATION else "archivage_compte"
        etat_succes = "Désactivé" if mode == MODE_DESACTIVATION else "Archivé"

        indexed_rows = [(i, row) for i, row in enumerate(self._rows) if row.user_dn is not None]
        labels = [row.identifiant for _, row in indexed_rows]
        self.validate_button.setEnabled(False)

        def run_one(entry: tuple[int, DepartRow]) -> None:
            _, row = entry
            if mode == MODE_DESACTIVATION:
                self._desactivate_one(row, base_dn)
            else:
                self._archive_one(row, base_dn)

        def on_result(position: int, success: bool, message: str) -> None:
            i, row = indexed_rows[position]
            if success:
                self.audit_log.record(
                    action_type, row.identifiant, "succes", self.session_id,
                )
                self._set_table_row(i, row, etat=etat_succes)
            else:
                row.erreur = message
                self.audit_log.record(
                    action_type, row.identifiant, "echec", self.session_id, detail=message,
                )
                self._set_table_row(i, row)

        def on_finished() -> None:
            self.validate_button.setEnabled(True)
            self._refresh_pending_panel()
            QMessageBox.information(
                self,
                "Traitement terminé",
                f"{self.progress_panel.success_count}/{len(to_process)} compte(s) traité(s).",
            )

        self.progress_panel.finished.connect(on_finished, type=Qt.ConnectionType.SingleShotConnection)
        self.progress_panel.start(
            "Traitement des départs en cours…", indexed_rows, labels, run_one, on_item_result=on_result,
        )

    def _desactivate_one(self, row: DepartRow, base_dn: str) -> None:
        assert row.user_dn is not None
        for group_dn in row.groupe_dns:
            try:
                self.ad_connection.remove_user_from_group(row.user_dn, group_dn)
            except ADError:
                pass
        self.ad_connection.disable_account(row.user_dn)

    def _archive_one(self, row: DepartRow, base_dn: str) -> None:
        assert row.user_dn is not None
        for group_dn in row.groupe_dns:
            try:
                self.ad_connection.remove_user_from_group(row.user_dn, group_dn)
            except ADError:
                pass
        self.ad_connection.move_user(row.user_dn, self.config.ou_archive)
        self.audit_log.add_pending_deletion(
            user_dn=row.user_dn.split(",")[0] + "," + self.config.ou_archive,
            sam_account_name=row.identifiant,
            nom_complet=row.nom_complet or row.identifiant,
            session_id=self.session_id,
            delai_jours=self.delai_spin.value(),
        )

    # -- Suppressions en attente ----------------------------------------------

    def _refresh_pending_panel(self) -> None:
        from datetime import datetime, timedelta, timezone
        pending = self.audit_log.get_pending_deletions()
        total = len(pending)
        due = self.audit_log.count_due_deletions(self.config.delai_suppression_jours)

        if total == 0:
            self.pending_label.setText("Aucun compte en attente de suppression.")
            self.process_pending_button.setEnabled(False)
        else:
            self.pending_label.setText(
                f"{due} compte(s) à supprimer (délai écoulé) — {total} en attente au total."
            )
            self.process_pending_button.setEnabled(due > 0)

        self.pending_table.setRowCount(len(pending))
        for i, entry in enumerate(pending):
            self.pending_table.setItem(i, 0, QTableWidgetItem(entry["sam_account_name"]))
            self.pending_table.setItem(i, 1, QTableWidgetItem(entry["nom_complet"] or ""))
            moved_at = entry["moved_at"][:10]
            self.pending_table.setItem(i, 2, QTableWidgetItem(moved_at))
            delai = entry.get("delai_jours")
            if delai is None:
                delai = self.config.delai_suppression_jours
            try:
                dt = datetime.fromisoformat(entry["moved_at"])
                echeance = (dt + timedelta(days=delai)).strftime("%Y-%m-%d")
            except ValueError:
                echeance = "?"
            self.pending_table.setItem(i, 3, QTableWidgetItem(f"{echeance}  ({delai} j.)"))
        self.cancel_pending_button.setEnabled(False)

    def _on_process_pending_clicked(self) -> None:
        due = self.audit_log.get_due_deletions(self.config.delai_suppression_jours)
        if not due:
            self._refresh_pending_panel()
            return

        reply = QMessageBox.question(
            self,
            "Supprimer définitivement",
            f"{len(due)} compte(s) vont être supprimés définitivement de l'AD. Confirmer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        labels = [entry["sam_account_name"] for entry in due]
        self.process_pending_button.setEnabled(False)

        def run_one(entry: dict) -> None:
            self.ad_connection.delete_user(entry["user_dn"])

        def on_result(position: int, success: bool, message: str) -> None:
            entry = due[position]
            sam = entry["sam_account_name"]
            if success:
                self.audit_log.remove_pending_deletion(entry["user_dn"])
                self.audit_log.record(
                    "suppression_compte", sam, "succes", self.session_id,
                )
                # Hygiène : pas de mot de passe recouvrable pour un compte
                # qui n'existe plus.
                self.password_vault.delete(sam)
            else:
                self.audit_log.record(
                    "suppression_compte", sam, "echec", self.session_id, detail=message,
                )

        def on_finished() -> None:
            self._refresh_pending_panel()
            QMessageBox.information(
                self,
                "Suppressions terminées",
                f"{self.pending_progress_panel.success_count}/{len(due)} compte(s) supprimé(s).",
            )

        self.pending_progress_panel.finished.connect(on_finished, type=Qt.ConnectionType.SingleShotConnection)
        self.pending_progress_panel.start(
            "Suppression des comptes échus en cours…", due, labels, run_one, on_item_result=on_result,
        )

    def _on_pending_selection_changed(self) -> None:
        self.cancel_pending_button.setEnabled(bool(self.pending_table.selectionModel().selectedRows()))

    def _on_cancel_pending_clicked(self) -> None:
        rows = self.pending_table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        pending = self.audit_log.get_pending_deletions()
        if idx >= len(pending):
            return
        entry = pending[idx]
        sam = entry["sam_account_name"]
        reply = QMessageBox.question(
            self,
            "Annuler la suppression",
            f"Retirer {sam} de la file d'attente de suppression ?\nLe compte restera dans l'OU d'archivage.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.audit_log.remove_pending_deletion(entry["user_dn"])
        self.audit_log.record("annulation_suppression", sam, "succes", self.session_id)
        self._refresh_pending_panel()

    # -- Annulation -----------------------------------------------------------

    def _on_cancel_clicked(self) -> None:
        self._rows = []
        self.preview_table.setRowCount(0)
        self.validate_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.info_label.setText("")
