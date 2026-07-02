"""Journal d'actions (§11) — table en lecture seule, filtres, export CSV."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QDate

from edusync_ad.core.audit import AuditLog

COLUMNS = [
    "Horodatage",
    "Action",
    "Compte",
    "Effectué par",
    "OU source",
    "OU destination",
    "Résultat",
    "Simulation",
    "Détail",
]

ACTION_TYPES = [
    "",
    "creation_compte",
    "migration_compte",
    "desactivation_compte",
    "archivage_compte",
    "suppression_compte",
    "annulation_suppression",
    "reinitialisation_mdp",
    "modification_attribut",
    "deplacement_compte",
    "ajout_groupe",
    "retrait_groupe",
    "activation_compte",
    "creation_ou",
    "renommage_ou",
    "suppression_ou",
    "creation_groupe",
    "creation_utilisateur_manuel",
]

ACTION_LABELS = {
    "": "(tous)",
    "creation_compte": "Création de compte",
    "migration_compte": "Migration",
    "desactivation_compte": "Désactivation",
    "archivage_compte": "Archivage (suppression différée)",
    "suppression_compte": "Suppression définitive",
    "annulation_suppression": "Annulation suppression",
    "reinitialisation_mdp": "Réinitialisation MDP",
    "modification_attribut": "Modification attribut",
    "deplacement_compte": "Changement d'OU",
    "ajout_groupe": "Ajout à un groupe",
    "retrait_groupe": "Retrait d'un groupe",
    "activation_compte": "Activation de compte",
    "creation_ou": "Création d'OU",
    "renommage_ou": "Renommage d'OU",
    "suppression_ou": "Suppression d'OU",
    "creation_groupe": "Création de groupe",
    "creation_utilisateur_manuel": "Création manuelle d'utilisateur",
}


class AuditPage(QWidget):
    def __init__(self, audit_log: AuditLog, parent=None) -> None:
        super().__init__(parent)
        self.audit_log = audit_log
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        # -- Filtres ----------------------------------------------------------
        filter_group = QGroupBox("Filtres")
        filter_layout = QHBoxLayout(filter_group)

        date_form = QFormLayout()
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        date_form.addRow("Du :", self.date_from)
        date_form.addRow("Au :", self.date_to)
        filter_layout.addLayout(date_form)

        type_form = QFormLayout()
        self.action_combo = QComboBox()
        for key in ACTION_TYPES:
            self.action_combo.addItem(ACTION_LABELS.get(key, key), key)
        self.resultat_combo = QComboBox()
        self.resultat_combo.addItem("(tous)", "")
        self.resultat_combo.addItem("Succès", "succes")
        self.resultat_combo.addItem("Échec", "echec")
        type_form.addRow("Type d'action :", self.action_combo)
        type_form.addRow("Résultat :", self.resultat_combo)
        filter_layout.addLayout(type_form)

        btn_col = QVBoxLayout()
        self.filter_btn = QPushButton("Appliquer les filtres")
        self.filter_btn.clicked.connect(self.refresh)
        self.reset_btn = QPushButton("Réinitialiser")
        self.reset_btn.clicked.connect(self._reset_filters)
        btn_col.addWidget(self.filter_btn)
        btn_col.addWidget(self.reset_btn)
        btn_col.addStretch()
        filter_layout.addLayout(btn_col)

        # -- Tableau ----------------------------------------------------------
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        # -- Barre du bas -----------------------------------------------------
        bottom_row = QHBoxLayout()
        self.count_label = QLabel("0 entrée(s)")
        refresh_btn = QPushButton("Actualiser")
        refresh_btn.clicked.connect(self.refresh)
        export_btn = QPushButton("Exporter en CSV")
        export_btn.clicked.connect(self._export)
        bottom_row.addWidget(self.count_label)
        bottom_row.addStretch()
        bottom_row.addWidget(refresh_btn)
        bottom_row.addWidget(export_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(filter_group)
        layout.addWidget(self.table)
        layout.addLayout(bottom_row)

    def _reset_filters(self) -> None:
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_to.setDate(QDate.currentDate())
        self.action_combo.setCurrentIndex(0)
        self.resultat_combo.setCurrentIndex(0)
        self.refresh()

    def refresh(self) -> None:
        date_from = self.date_from.date().toString("yyyy-MM-dd") + "T00:00:00+00:00"
        date_to = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59+00:00"
        action_type = self.action_combo.currentData() or None
        resultat = self.resultat_combo.currentData() or None

        entries = self.audit_log.query(
            date_from=date_from,
            date_to=date_to,
            action_type=action_type,
            resultat=resultat,
        )

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.timestamp,
                ACTION_LABELS.get(entry.action_type, entry.action_type),
                entry.compte,
                entry.utilisateur or "—",
                entry.ou_source or "",
                entry.ou_destination or "",
                "Succès" if entry.resultat == "succes" else "Échec",
                "Oui" if entry.simulation else "Non",
                entry.detail,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

        self.count_label.setText(f"{len(entries)} entrée(s)")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le journal", "journal_actions.csv", "CSV (*.csv)"
        )
        if not path:
            return
        self.audit_log.export_csv(Path(path))
        QMessageBox.information(self, "Export terminé", f"Journal exporté vers {path}")
