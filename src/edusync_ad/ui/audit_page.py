"""Journal d'actions (§11) — table en lecture seule + export CSV."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.audit import AuditLog

COLUMNS = [
    "Horodatage",
    "Action",
    "Compte",
    "OU source",
    "OU destination",
    "Résultat",
    "Simulation",
    "Détail",
]


class AuditPage(QWidget):
    def __init__(self, audit_log: AuditLog, parent=None) -> None:
        super().__init__(parent)
        self.audit_log = audit_log

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        refresh_button = QPushButton("Actualiser")
        refresh_button.clicked.connect(self.refresh)
        export_button = QPushButton("Exporter en CSV")
        export_button.clicked.connect(self._export)

        toolbar = QHBoxLayout()
        toolbar.addWidget(refresh_button)
        toolbar.addWidget(export_button)
        toolbar.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self) -> None:
        entries = self.audit_log.query()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.timestamp,
                entry.action_type,
                entry.compte,
                entry.ou_source or "",
                entry.ou_destination or "",
                entry.resultat,
                "Oui" if entry.simulation else "Non",
                entry.detail,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le journal", "journal_actions.csv", "CSV (*.csv)"
        )
        if not path:
            return
        self.audit_log.export_csv(Path(path))
        QMessageBox.information(self, "Export terminé", f"Journal exporté vers {path}")
