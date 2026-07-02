"""Fenêtre de journal en direct pour diagnostiquer les connexions AD."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from ldap3.utils.log import (
    EXTENDED,
    set_library_log_activation_level,
    set_library_log_detail_level,
)

_LOG_FORMAT = logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", "%H:%M:%S")


class _QtLogHandler(QObject, logging.Handler):
    log_emitted = pyqtSignal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(_LOG_FORMAT)

    def emit(self, record: logging.LogRecord) -> None:
        self.log_emitted.emit(self.format(record))


class DebugConsole(QDialog):
    """Affiche en direct les logs de connexion AD (edusync_ad + ldap3)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Journal de connexion (mode debug)")
        self.setMinimumSize(680, 420)
        self.setModal(False)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setStyleSheet("font-family: monospace; font-size: 11px;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addWidget(buttons)

        self._handler = _QtLogHandler()
        self._handler.log_emitted.connect(self.text.appendPlainText)
        self._attached = False

    def start(self, level: int = logging.DEBUG) -> None:
        """Active la capture des logs edusync_ad et ldap3 vers cette fenêtre."""
        if self._attached:
            return

        set_library_log_activation_level(level)
        set_library_log_detail_level(EXTENDED)

        for logger_name in ("edusync_ad", "ldap3"):
            log = logging.getLogger(logger_name)
            log.setLevel(level)
            log.addHandler(self._handler)

        self._attached = True
        self.text.appendPlainText("— Mode debug activé —")

    def stop(self) -> None:
        if not self._attached:
            return
        for logger_name in ("edusync_ad", "ldap3"):
            logging.getLogger(logger_name).removeHandler(self._handler)
        self._attached = False

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.stop()
        super().closeEvent(event)
