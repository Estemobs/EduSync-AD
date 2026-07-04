"""Panneau de progression réutilisable, embarqué directement sur la page,
pour les actions par lot sur l'AD (création de comptes, migration, départs,
réinitialisation de mots de passe).

Exécute `run_one(item)` pour chaque élément dans un thread séparé pour ne
jamais geler l'UI, avec une barre de progression et le détail par ligne
coloré selon le résultat.
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.exceptions import ADError

logger = logging.getLogger("edusync_ad.batch")

_COLOR_SUCCESS = QColor("#2f9e56")
_COLOR_FAILURE = QColor("#d64545")


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
            except ADError as exc:
                # Refus AD attendu (droits, doublon, referral…) — message
                # métier direct, pas une anomalie de code.
                self.item_done.emit(index, False, str(exc))
            except Exception as exc:
                # Bug de programmation (TypeError, AttributeError…) : à ne
                # jamais confondre avec un refus AD légitime. La stacktrace
                # complète va dans le journal debug ; l'utilisateur ne voit
                # qu'un message générique mais distinct visuellement.
                logger.exception("Erreur interne inattendue en traitant %r", item)
                self.item_done.emit(index, False, f"Erreur interne : {exc}")
            else:
                self.item_done.emit(index, True, "")
        self.all_done.emit()


class BatchProgressPanel(QWidget):
    """Panneau de progression embarqué (invisible tant qu'aucune action n'a
    démarré). `on_item_result(position, success, message)` est appelé sur le
    thread principal pour chaque élément — c'est là qu'il faut journaliser
    l'action (AuditLog) et mettre à jour le tableau d'aperçu. Le signal
    `finished` est émis une fois tous les éléments traités (ou annulés).
    """

    finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: Sequence = []
        self._labels: Sequence[str] = []
        self._on_item_result: Callable[[int, bool, str], None] | None = None
        self._worker: _BatchWorker | None = None
        self.success_count = 0
        self.failure_count = 0

        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight: 600;")
        self.status_label = QLabel("")
        self.progress = QProgressBar()
        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(240)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self._on_cancel)

        header_row = QHBoxLayout()
        header_row.addWidget(self.title_label)
        header_row.addStretch()
        header_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(header_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.list_widget)

        self.setVisible(False)

        app = QApplication.instance()
        if app is not None:
            # Filet de sécurité : si l'app se ferme pendant qu'un lot tourne,
            # Qt détruirait le QThread encore actif → crash sec ("QThread:
            # Destroyed while thread is still running"). On l'arrête proprement.
            app.aboutToQuit.connect(self._on_app_quit)

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(
        self,
        title: str,
        items: Sequence,
        labels: Sequence[str],
        run_one: Callable[[object], None],
        on_item_result: Callable[[int, bool, str], None] | None = None,
    ) -> None:
        if self.is_running():
            QMessageBox.warning(
                self, "Traitement en cours",
                "Une action par lot est déjà en cours sur cette page — attendez sa fin avant d'en lancer une autre.",
            )
            return

        self.title_label.setText(title)
        self._items = items
        self._labels = labels
        self._on_item_result = on_item_result
        self.success_count = 0
        self.failure_count = 0

        self.progress.setRange(0, max(len(items), 1))
        self.progress.setValue(0)
        self.list_widget.clear()
        for label in labels:
            self.list_widget.addItem(QListWidgetItem(f"⏳  {label}"))
        self.status_label.setText(f"0 / {len(items)}")
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.setVisible(True)

        self._worker = _BatchWorker(items, run_one)
        self._worker.item_done.connect(self._on_item_done)
        self._worker.all_done.connect(self._on_all_done)
        # Signal natif de QThread (fin réelle du thread système), distinct de
        # all_done (fin logique du traitement) — assure le nettoyage Qt propre
        # de l'objet thread une fois qu'il a effectivement terminé.
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_item_done(self, index: int, success: bool, message: str) -> None:
        item = self.list_widget.item(index)
        label = self._labels[index]
        if success:
            self.success_count += 1
            item.setText(f"✓  {label}")
            item.setForeground(_COLOR_SUCCESS)
        else:
            self.failure_count += 1
            item.setText(f"✗  {label} — {message}" if message else f"✗  {label}")
            item.setForeground(_COLOR_FAILURE)
        done = self.success_count + self.failure_count
        self.progress.setValue(done)
        self.status_label.setText(
            f"{done} / {len(self._items)}  ({self.success_count} réussi(s), {self.failure_count} échoué(s))"
        )
        self.list_widget.setCurrentRow(index)
        self.list_widget.scrollToItem(item)
        if self._on_item_result:
            self._on_item_result(index, success, message)

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
        self.cancel_button.setEnabled(False)

    def _on_all_done(self) -> None:
        self.cancel_button.setVisible(False)
        self.status_label.setText(self.status_label.text() + "  — Terminé")
        self.finished.emit()

    def _on_app_quit(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
