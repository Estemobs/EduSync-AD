"""Fenêtre de journal en direct affichée depuis l'écran de connexion."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from edusync_ad.ui.log_manager import AppLogManager
from edusync_ad.ui.log_view_widget import LogViewWidget


class DebugConsole(QDialog):
    """Affiche en direct les logs de connexion AD (edusync_ad + ldap3)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Journal de connexion (mode debug)")
        self.setMinimumSize(680, 420)
        self.setModal(False)

        self._view = LogViewWidget(self)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self._view)
        layout.addWidget(buttons)

    def start(self) -> None:
        """Active le mode debug (détail LDAP étendu) pendant la connexion."""
        self._view.debug_checkbox.setChecked(True)
        AppLogManager.instance().set_debug(True)
