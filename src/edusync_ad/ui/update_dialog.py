"""Dialogue de mise à jour — vérifie et installe la dernière release."""

from __future__ import annotations

import platform

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from edusync_ad.core.updater import check_for_update, download_and_install, CURRENT_VERSION


class _CheckWorker(QThread):
    done = pyqtSignal(object)

    def run(self):
        self.done.emit(check_for_update())


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)
    done = pyqtSignal(bool)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        ok = download_and_install(self._url, progress_callback=self.progress.emit)
        self.done.emit(ok)


class UpdateDialog(QDialog):
    def __init__(self, parent=None, initial_info: dict | None | object = "unset"):
        super().__init__(parent)
        self.setWindowTitle("Mises à jour")
        self.setMinimumSize(520, 420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._info: dict | None = None
        self._worker = None

        layout = QVBoxLayout(self)

        self.status_label = QLabel(f"Version actuelle : <b>v{CURRENT_VERSION}</b><br>Vérification en cours…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.notes_browser = QTextBrowser()
        self.notes_browser.setOpenExternalLinks(True)
        self.notes_browser.setVisible(False)
        layout.addWidget(self.notes_browser, stretch=1)

        self.buttons = QDialogButtonBox()
        self.install_btn = self.buttons.addButton("Installer la mise à jour", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_btn = self.buttons.addButton("Fermer", QDialogButtonBox.ButtonRole.RejectRole)
        self.install_btn.setVisible(False)
        self.install_btn.clicked.connect(self._on_install)
        self.close_btn.clicked.connect(self.reject)
        layout.addWidget(self.buttons)

        if initial_info == "unset":
            self._start_check()
        else:
            # Résultat déjà connu (vérification automatique au lancement) :
            # on évite un second appel réseau.
            self._on_check_done(initial_info)

    def _start_check(self):
        self._worker = _CheckWorker()
        self._worker.done.connect(self._on_check_done)
        self._worker.start()

    def _on_check_done(self, info: dict | None):
        if info is None:
            self.status_label.setText(
                f"Version actuelle : <b>v{CURRENT_VERSION}</b><br>"
                "Vous utilisez déjà la dernière version."
            )
            return

        self._info = info
        self.status_label.setText(
            f"Version actuelle : <b>v{info['current']}</b><br>"
            f"Nouvelle version disponible : <b>{info['version']}</b>"
        )

        if info.get("release_notes"):
            self.notes_browser.setMarkdown(info["release_notes"])
            self.notes_browser.setVisible(True)

        if info.get("download_url"):
            self.install_btn.setVisible(True)
        else:
            self.status_label.setText(
                self.status_label.text()
                + "<br><br>Aucun paquet compatible trouvé pour cette plateforme — "
                "téléchargez-le manuellement depuis les Releases."
            )

    def _on_install(self):
        if not self._info or not self._info.get("download_url"):
            return

        self.install_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status_label.setText("Téléchargement en cours…")

        self._dl_worker = _DownloadWorker(self._info["download_url"])
        self._dl_worker.progress.connect(self.progress.setValue)
        self._dl_worker.done.connect(self._on_download_done)
        self._dl_worker.start()

    def _on_download_done(self, ok: bool):
        if ok:
            if platform.system() == "Windows":
                QMessageBox.information(
                    self,
                    "Mise à jour prête",
                    "La mise à jour va s'appliquer. L'application va redémarrer automatiquement.",
                )
                import sys
                sys.exit(0)
            else:
                QMessageBox.information(
                    self,
                    "Mise à jour installée",
                    "La mise à jour a été installée avec succès. Fermez et relancez "
                    "l'application pour utiliser la nouvelle version.",
                )
                self.accept()
        else:
            QMessageBox.warning(
                self, "Erreur",
                "L'installation a échoué. Si l'application est installée en mode système "
                "(flatpak --system), une fenêtre d'autorisation aurait dû apparaître — "
                "réessayez, ou mettez à jour manuellement avec :\n\n"
                "flatpak update org.edusync.AD",
            )
            self.install_btn.setEnabled(True)
            self.progress.setVisible(False)
