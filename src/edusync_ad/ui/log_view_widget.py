"""Widget réutilisable affichant le journal applicatif en direct.

Utilisé à la fois par la fenêtre de debug de l'écran de connexion et par la
page « Journal de l'application » (menu latéral, §12).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.ui.log_manager import AppLogManager


class LogViewWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._manager = AppLogManager.instance()

        self.debug_checkbox = QCheckBox("Mode debug (détails techniques LDAP)")
        self.debug_checkbox.setChecked(self._manager.is_debug_enabled())
        self.debug_checkbox.toggled.connect(self._manager.set_debug)

        self.copy_button = QPushButton("Copier")
        self.copy_button.clicked.connect(self._on_copy)
        self.clear_button = QPushButton("Vider le journal")
        self.clear_button.clicked.connect(self._on_clear)
        self.export_button = QPushButton("Exporter…")
        self.export_button.clicked.connect(self._on_export)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.debug_checkbox)
        toolbar.addStretch()
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.export_button)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setStyleSheet("font-family: monospace; font-size: 11px;")
        self.text.setPlainText("\n".join(self._manager.lines()))
        self._scroll_to_bottom()

        self._manager.line_emitted.connect(self._append)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.text)

    def _append(self, line: str) -> None:
        self.text.appendPlainText(line)

    def _scroll_to_bottom(self) -> None:
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def _on_clear(self) -> None:
        self._manager.clear()
        self.text.clear()

    def _on_export(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Exporter le journal", "edusync_ad_journal.txt", "Texte (*.txt)"
        )
        if not path_str:
            return
        Path(path_str).write_text(self.text.toPlainText(), encoding="utf-8")
