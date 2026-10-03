"""Panneau de progression asynchrone (T0) — Version moderne du BatchProgressPanel.

Utilise AsyncADConnection + JobSignals pour :
- Vraie parallélisation via QThreadPool
- Progression granulaire par opération (pas seulement par élément)
- Annulation granulaire (par job ou tout)
- UI 100% responsive même sous charge lourde
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
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

from edusync_ad.core.ad.async_connection import AsyncADConnection, Job, JobSignals, JobStatus

logger = logging.getLogger("edusync_ad.batch.async")

_COLOR_SUCCESS = QColor("#2f9e56")
_COLOR_FAILURE = QColor("#d64545")
_COLOR_RUNNING = QColor("#3a7bd5")
_COLOR_PENDING = QColor("#888888")


class AsyncBatchProgressPanel(QWidget):
    """Panneau de progression pour opérations asynchrones par lot.
    
    Gère plusieurs jobs concurrents via AsyncADConnection.
    Chaque ligne = un job (pas un élément séquentiel).
    
    Signaux:
        finished: émis quand tous les jobs sont terminés (succès/échec/annulé)
    """
    
    finished = pyqtSignal()
    
    def __init__(self, async_ad: AsyncADConnection, parent=None) -> None:
        super().__init__(parent)
        self._async_ad = async_ad
        self._jobs: list[Job] = []
        self._labels: list[str] = []
        self._on_job_result: Callable[[Job], None] | None = None
        self._completed_count = 0
        self._total_count = 0
        self._success_count = 0
        self._failure_count = 0
        
        # Connexion aux signaux globaux
        self._async_ad.signals.progress.connect(self._on_job_progress)
        self._async_ad.signals.finished.connect(self._on_job_finished)
        self._async_ad.signals.error.connect(self._on_job_error)
        self._async_ad.signals.cancelled.connect(self._on_job_cancelled)
        
        self._build_ui()
        
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

    def _build_ui(self) -> None:
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight: 600;")
        self.status_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p% - %v/%m")
        
        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(280)
        self.list_widget.setAlternatingRowColors(True)
        
        self.cancel_button = QPushButton("Annuler tout")
        self.cancel_button.clicked.connect(self._on_cancel_all)
        
        self.cancel_selected_button = QPushButton("Annuler sélection")
        self.cancel_selected_button.clicked.connect(self._on_cancel_selected)
        self.cancel_selected_button.setEnabled(False)
        
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        
        header_row = QHBoxLayout()
        header_row.addWidget(self.title_label)
        header_row.addStretch()
        header_row.addWidget(self.cancel_selected_button)
        header_row.addWidget(self.cancel_button)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(header_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.list_widget)
        
        self.setVisible(False)

    def start(
        self,
        title: str,
        jobs: Sequence[Job],
        labels: Sequence[str],
        on_job_result: Callable[[Job], None] | None = None,
    ) -> None:
        """Démarre le traitement d'une liste de jobs."""
        if self._async_ad._thread_pool.activeThreadCount() > 0 and self.isVisible():
            QMessageBox.warning(
                self, "Traitement en cours",
                "Une action asynchrone est déjà en cours — attendez sa fin."
            )
            return
        
        if len(jobs) != len(labels):
            raise ValueError("jobs et labels doivent avoir la même longueur")
        
        self.title_label.setText(title)
        self._jobs = list(jobs)
        self._labels = list(labels)
        self._on_job_result = on_job_result
        self._completed_count = 0
        self._total_count = len(jobs)
        self._success_count = 0
        self._failure_count = 0
        
        self.progress.setRange(0, self._total_count)
        self.progress.setValue(0)
        self.list_widget.clear()
        
        for i, label in enumerate(self._labels):
            item = QListWidgetItem(f"⏳  {label}")
            item.setForeground(_COLOR_PENDING)
            item.setData(Qt.ItemDataRole.UserRole, self._jobs[i].id)
            self.list_widget.addItem(item)
        
        self.status_label.setText(f"0 / {self._total_count}  (en attente...)")
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.setVisible(True)
        
        # Les jobs sont déjà soumis à la queue via AsyncADConnection
        # On ne fait qu'attendre leurs signaux ici

    @pyqtSlot(str, int, int, str)
    def _on_job_progress(self, job_id: str, current: int, total: int, message: str) -> None:
        """Appelé pour chaque mise à jour de progression d'un job."""
        row = self._find_row_by_job_id(job_id)
        if row >= 0:
            item = self.list_widget.item(row)
            label = self._labels[row]
            if total > 0:
                pct = int(current * 100 / total)
                item.setText(f"⟳ {pct}%  {label} — {message}")
            else:
                item.setText(f"⟳  {label} — {message}")
            item.setForeground(_COLOR_RUNNING)

    @pyqtSlot(str, object)
    def _on_job_finished(self, job_id: str, result: object) -> None:
        """Appelé quand un job se termine avec succès."""
        self._handle_job_done(job_id, True, result, "")

    @pyqtSlot(str, Exception)
    def _on_job_error(self, job_id: str, exc: Exception) -> None:
        """Appelé quand un job échoue."""
        self._handle_job_done(job_id, False, None, str(exc))

    @pyqtSlot(str)
    def _on_job_cancelled(self, job_id: str) -> None:
        """Appelé quand un job est annulé."""
        self._handle_job_done(job_id, False, None, "Annulé")

    def _handle_job_done(self, job_id: str, success: bool, result: object, error_msg: str) -> None:
        row = self._find_row_by_job_id(job_id)
        if row < 0:
            return
        
        job = self._jobs[row]
        label = self._labels[row]
        item = self.list_widget.item(row)
        
        self._completed_count += 1
        if success:
            self._success_count += 1
            item.setText(f"✓  {label}")
            item.setForeground(_COLOR_SUCCESS)
        else:
            self._failure_count += 1
            item.setText(f"✗  {label} — {error_msg}" if error_msg else f"✗  {label}")
            item.setForeground(_COLOR_FAILURE)
        
        # Stocker le résultat sur le job pour le callback
        if success:
            job.result = result
        else:
            job.error = Exception(error_msg) if error_msg else Exception("Échec")
        job.status = JobStatus.COMPLETED if success else JobStatus.FAILED
        
        self.progress.setValue(self._completed_count)
        self.status_label.setText(
            f"{self._completed_count} / {self._total_count}  "
            f"({self._success_count} réussi(s), {self._failure_count} échoué(s))"
        )
        
        # Callback utilisateur
        if self._on_job_result:
            self._on_job_result(job)
        
        # Vérifier si tout est fini
        if self._completed_count >= self._total_count:
            self._on_all_done()

    def _find_row_by_job_id(self, job_id: str) -> int:
        for i, job in enumerate(self._jobs):
            if job.id == job_id:
                return i
        return -1

    def _on_selection_changed(self) -> None:
        has_selection = len(self.list_widget.selectedItems()) > 0
        self.cancel_selected_button.setEnabled(has_selection)

    def _on_cancel_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            job_id = item.data(Qt.ItemDataRole.UserRole)
            self._async_ad.cancel_job(job_id)

    def _on_cancel_all(self) -> None:
        self._async_ad.cancel_all_jobs()
        self.cancel_button.setEnabled(False)
        self.cancel_selected_button.setEnabled(False)

    def _on_all_done(self) -> None:
        self.cancel_button.setVisible(False)
        self.cancel_selected_button.setVisible(False)
        self.status_label.setText(self.status_label.text() + "  — Terminé")
        self.finished.emit()

    def _on_app_quit(self) -> None:
        if self.isVisible():
            self._async_ad.cancel_all_jobs()
            self._async_ad._thread_pool.waitForDone(5000)

    # -- API de compatibilité avec l'ancien BatchProgressPanel ---------------
    
    @property
    def success_count(self) -> int:
        return self._success_count
    
    @property
    def failure_count(self) -> int:
        return self._failure_count

    def is_running(self) -> bool:
        return self.isVisible() and self._completed_count < self._total_count