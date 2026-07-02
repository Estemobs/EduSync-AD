"""Dialogue de progression réutilisable pour les actions par lot sur l'AD
(création de comptes, migration, départs, réinitialisation de mots de passe).

Exécute `run_one(item)` pour chaque élément dans un thread séparé pour ne
jamais geler l'UI, avec une barre de progression et le détail par ligne.
"""

from __future__ import annotations

from typing import Callable, Sequence

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
)


class _BatchWorker(QThread):
    item_done = pyqtSignal(int, bool, str)  # index, succès, message
    all_done = pyqtSignal()

    def __init__(self, items: Sequence, run_one: Callable[[object], None]) -> None:
        super().__init__()
        self._items = items
        self._run_one = run_one
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for index, item in enumerate(self._items):
            if self._cancelled:
                self.item_done.emit(index, False, "Annulé par l'utilisateur")
                continue
            try:
                self._run_one(item)
            except Exception as exc:  # remonté à l'appelant via le signal
                self.item_done.emit(index, False, str(exc))
            else:
                self.item_done.emit(index, True, "")
        self.all_done.emit()


class BatchProgressDialog(QDialog):
    """Affiche la progression d'une opération par lot sur l'AD.

    `on_item_result(position, success, message)` est appelé sur le thread
    principal pour chaque élément — c'est là qu'il faut journaliser l'action
    (AuditLog) et mettre à jour le tableau d'aperçu.
    """

    def __init__(
        self,
        title: str,
        items: Sequence,
        labels: Sequence[str],
        run_one: Callable[[object], None],
        on_item_result: Callable[[int, bool, str], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 420)
        self.setModal(True)

        self._items = items
        self._labels = labels
        self._on_item_result = on_item_result
        self.success_count = 0
        self.failure_count = 0

        self.status_label = QLabel(f"0 / {len(items)}")
        self.progress = QProgressBar()
        self.progress.setRange(0, max(len(items), 1))
        self.list_widget = QListWidget()
        for label in labels:
            self.list_widget.addItem(QListWidgetItem(f"⏳  {label}"))

        self.buttons = QDialogButtonBox()
        self.cancel_button = self.buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        self.close_button = self.buttons.addButton("Fermer", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        self.close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.buttons)

        self._worker = _BatchWorker(items, run_one)
        self._worker.item_done.connect(self._on_item_done)
        self._worker.all_done.connect(self._on_all_done)

    def start(self) -> None:
        self._worker.start()

    def _on_item_done(self, index: int, success: bool, message: str) -> None:
        item = self.list_widget.item(index)
        label = self._labels[index]
        if success:
            self.success_count += 1
            item.setText(f"✓  {label}")
        else:
            self.failure_count += 1
            item.setText(f"✗  {label} — {message}" if message else f"✗  {label}")
        done = self.success_count + self.failure_count
        self.progress.setValue(done)
        self.status_label.setText(
            f"{done} / {len(self._items)}  ({self.success_count} réussi(s), {self.failure_count} échoué(s))"
        )
        self.list_widget.setCurrentRow(index)
        if self._on_item_result:
            self._on_item_result(index, success, message)

    def _on_cancel(self) -> None:
        self._worker.cancel()
        self.cancel_button.setEnabled(False)

    def _on_all_done(self) -> None:
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.status_label.setText(self.status_label.text() + "  — Terminé")
