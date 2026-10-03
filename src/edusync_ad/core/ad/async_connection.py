"""Couche LDAP Asynchrone (T0) — Remplace RLock + threads manuels par queue de jobs.

Utilise QThreadPool pour exécuter les opérations LDAP en arrière-plan sans bloquer
l'UI, avec signaux Qt pour progression, fin, erreur, annulation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError

logger = logging.getLogger("edusync_ad.ad.async")


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """Unité de travail asynchrone pour une opération LDAP."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    coro: Callable = None  # Callable qui prend (progress_callback) et retourne result
    progress_cb: Optional[Callable[[int, int, str], None]] = None  # (current, total, message)
    done_cb: Optional[Callable[[Any], None]] = None  # (result) -> None
    error_cb: Optional[Callable[[Exception], None]] = None  # (exception) -> None
    cancelled_cb: Optional[Callable[[], None]] = None  # () -> None
    status: JobStatus = JobStatus.PENDING
    result: Any = None
    error: Optional[Exception] = None
    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True
        self.status = JobStatus.CANCELLED

    def is_cancelled(self) -> bool:
        return self._cancelled


class JobSignals(QObject):
    """Signaux Qt émis par un JobRunner (doit hériter de QObject pour les signaux)."""
    progress = pyqtSignal(str, int, int, str)      # job_id, current, total, message
    finished = pyqtSignal(str, object)             # job_id, result
    error = pyqtSignal(str, Exception)             # job_id, exception
    cancelled = pyqtSignal(str)                    # job_id


class _JobRunner(QRunnable):
    """QRunnable qui exécute un Job dans le thread pool."""
    
    def __init__(self, job: Job, signals: JobSignals, ad_connection: ADConnection):
        super().__init__()
        self.job = job
        self.signals = signals
        self.ad_connection = ad_connection
        self.setAutoDelete(True)

    def run(self) -> None:
        if self.job.is_cancelled():
            self.signals.cancelled.emit(self.job.id)
            if self.job.cancelled_cb:
                self.job.cancelled_cb()
            return

        self.job.status = JobStatus.RUNNING
        
        def progress_callback(current: int, total: int, message: str = "") -> None:
            if self.job.is_cancelled():
                raise InterruptedError("Job annulé")
            self.signals.progress.emit(self.job.id, current, total, message)
            if self.job.progress_cb:
                self.job.progress_cb(current, total, message)

        try:
            # Exécute la coroutine (qui est en fait une fonction synchrone ici)
            result = self.job.coro(progress_callback)
            self.job.result = result
            self.job.status = JobStatus.COMPLETED
            self.signals.finished.emit(self.job.id, result)
            if self.job.done_cb:
                self.job.done_cb(result)
        except InterruptedError:
            self.job.status = JobStatus.CANCELLED
            self.signals.cancelled.emit(self.job.id)
            if self.job.cancelled_cb:
                self.job.cancelled_cb()
        except Exception as exc:
            self.job.error = exc
            self.job.status = JobStatus.FAILED
            self.signals.error.emit(self.job.id, exc)
            if self.job.error_cb:
                self.job.error_cb(exc)


class AsyncADConnection:
    """Wrapper asynchrone autour de ADConnection.
    
    Utilise QThreadPool pour exécuter les opérations LDAP sans bloquer l'UI.
    Chaque opération est soumise comme un Job avec callbacks de progression.
    """
    
    def __init__(self, ad_connection: ADConnection, max_workers: int = 4):
        self._ad = ad_connection
        self._thread_pool = QThreadPool.globalInstance()
        self._thread_pool.setMaxThreadCount(max_workers)
        self._signals = JobSignals()
        self._jobs: dict[str, Job] = {}
        self._job_runners: dict[str, _JobRunner] = {}

    @property
    def signals(self) -> JobSignals:
        return self._signals

    def _submit_job(self, job: Job) -> str:
        """Soumet un job à la queue d'exécution."""
        self._jobs[job.id] = job
        runner = _JobRunner(job, self._signals, self._ad)
        self._job_runners[job.id] = runner
        self._thread_pool.start(runner)
        return job.id

    def _wrap_operation(self, operation_name: str, operation_fn: Callable) -> Callable:
        """Wrapper pour ajouter logging et gestion d'erreurs standard."""
        def wrapped(progress_callback: Callable[[int, int, str], None]):
            progress_callback(0, 1, f"Démarrage : {operation_name}")
            try:
                result = operation_fn()
                progress_callback(1, 1, f"Terminé : {operation_name}")
                return result
            except Exception as exc:
                logger.error("Erreur dans %s : %s", operation_name, exc)
                raise
        return wrapped

    # -- Opérations de lecture -------------------------------------------------
    
    def search_existing_identifiers(self, base_dn: str) -> str:
        """Retourne job_id. Résultat via done_cb : set[str]."""
        job = Job(
            coro=self._wrap_operation("search_existing_identifiers", 
                lambda: self._ad.search_existing_identifiers(base_dn)),
        )
        return self._submit_job(job)

    def ou_exists(self, ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("ou_exists", 
                lambda: self._ad.ou_exists(ou_dn)),
        )
        return self._submit_job(job)

    def group_exists(self, group_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("group_exists",
                lambda: self._ad.group_exists(group_dn)),
        )
        return self._submit_job(job)

    def list_ous(self, base_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("list_ous",
                lambda: self._ad.list_ous(base_dn)),
        )
        return self._submit_job(job)

    def list_groups(self, base_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("list_groups",
                lambda: self._ad.list_groups(base_dn)),
        )
        return self._submit_job(job)

    def list_users_in_ou(self, ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("list_users_in_ou",
                lambda: self._ad.list_users_in_ou(ou_dn)),
        )
        return self._submit_job(job)

    def list_users_in_group(self, group_dn: str, base_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("list_users_in_group",
                lambda: self._ad.list_users_in_group(group_dn, base_dn)),
        )
        return self._submit_job(job)

    def get_user_attributes(self, user_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("get_user_attributes",
                lambda: self._ad.get_user_attributes(user_dn)),
        )
        return self._submit_job(job)

    def search_user_by_cn(self, cn: str, ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("search_user_by_cn",
                lambda: self._ad.search_user_by_cn(cn, ou_dn)),
        )
        return self._submit_job(job)

    def search_user_by_sam(self, sam_account_name: str, base_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("search_user_by_sam",
                lambda: self._ad.search_user_by_sam(sam_account_name, base_dn)),
        )
        return self._submit_job(job)

    def search_user_groups(self, user_dn: str, base_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("search_user_groups",
                lambda: self._ad.search_user_groups(user_dn, base_dn)),
        )
        return self._submit_job(job)

    def list_ou_contents(self, ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("list_ou_contents",
                lambda: self._ad.list_ou_contents(ou_dn)),
        )
        return self._submit_job(job)

    def ou_is_empty(self, ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("ou_is_empty",
                lambda: self._ad.ou_is_empty(ou_dn)),
        )
        return self._submit_job(job)

    def list_ou_children(self, ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("list_ou_children",
                lambda: self._ad.list_ou_children(ou_dn)),
        )
        return self._submit_job(job)

    # -- Opérations d'écriture -------------------------------------------------
    
    def create_user(self, dn: str, attributes: dict, *, password: str | None = None,
                    force_password_change: bool = False) -> str:
        job = Job(
            coro=self._wrap_operation("create_user",
                lambda: self._ad.create_user(dn, attributes, password=password, 
                                             force_password_change=force_password_change)),
        )
        return self._submit_job(job)

    def set_password(self, dn: str, password: str) -> str:
        job = Job(
            coro=self._wrap_operation("set_password",
                lambda: self._ad.set_password(dn, password)),
        )
        return self._submit_job(job)

    def enable_account(self, dn: str, *, force_password_change: bool = False) -> str:
        job = Job(
            coro=self._wrap_operation("enable_account",
                lambda: self._ad.enable_account(dn, force_password_change=force_password_change)),
        )
        return self._submit_job(job)

    def create_ou(self, dn: str, name: str) -> str:
        job = Job(
            coro=self._wrap_operation("create_ou",
                lambda: self._ad.create_ou(dn, name)),
        )
        return self._submit_job(job)

    def delete_ou(self, dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("delete_ou",
                lambda: self._ad.delete_ou(dn)),
        )
        return self._submit_job(job)

    def rename_ou(self, dn: str, new_name: str) -> str:
        job = Job(
            coro=self._wrap_operation("rename_ou",
                lambda: self._ad.rename_ou(dn, new_name)),
        )
        return self._submit_job(job)

    def create_group(self, dn: str, sam_account_name: str) -> str:
        job = Job(
            coro=self._wrap_operation("create_group",
                lambda: self._ad.create_group(dn, sam_account_name)),
        )
        return self._submit_job(job)

    def delete_group(self, dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("delete_group",
                lambda: self._ad.delete_group(dn)),
        )
        return self._submit_job(job)

    def add_user_to_group(self, user_dn: str, group_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("add_user_to_group",
                lambda: self._ad.add_user_to_group(user_dn, group_dn)),
        )
        return self._submit_job(job)

    def remove_user_from_group(self, user_dn: str, group_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("remove_user_from_group",
                lambda: self._ad.remove_user_from_group(user_dn, group_dn)),
        )
        return self._submit_job(job)

    def move_user(self, user_dn: str, new_ou_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("move_user",
                lambda: self._ad.move_user(user_dn, new_ou_dn)),
        )
        return self._submit_job(job)

    def disable_account(self, dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("disable_account",
                lambda: self._ad.disable_account(dn)),
        )
        return self._submit_job(job)

    def delete_user(self, dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("delete_user",
                lambda: self._ad.delete_user(dn)),
        )
        return self._submit_job(job)

    def update_user_attribute(self, user_dn: str, attribute: str, value: str | list[str]) -> str:
        job = Job(
            coro=self._wrap_operation("update_user_attribute",
                lambda: self._ad.update_user_attribute(user_dn, attribute, value)),
        )
        return self._submit_job(job)

    # -- Heures de connexion (M16) -----------------------------------------------

    def get_logon_hours(self, user_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("get_logon_hours",
                lambda: self._ad.get_logon_hours(user_dn)),
        )
        return self._submit_job(job)

    def set_logon_hours(self, user_dn: str, value: bytes) -> str:
        job = Job(
            coro=self._wrap_operation("set_logon_hours",
                lambda: self._ad.set_logon_hours(user_dn, value)),
        )
        return self._submit_job(job)

    def clear_logon_hours(self, user_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("clear_logon_hours",
                lambda: self._ad.clear_logon_hours(user_dn)),
        )
        return self._submit_job(job)

    def clear_script_path(self, user_dn: str) -> str:
        job = Job(
            coro=self._wrap_operation("clear_script_path",
                lambda: self._ad.clear_script_path(user_dn)),
        )
        return self._submit_job(job)

    def rename_user(self, user_dn: str, new_cn: str) -> str:
        job = Job(
            coro=self._wrap_operation("rename_user",
                lambda: self._ad.rename_user(user_dn, new_cn)),
        )
        return self._submit_job(job)

    # -- Photos (M12) -----------------------------------------------------------
    
    def get_user_photo(self, user_dn: str, *, thumbnail: bool = False) -> str:
        """Récupère la photo d'un utilisateur.
        
        Args:
            user_dn: DN de l'utilisateur
            thumbnail: Si True, récupère thumbnailPhoto, sinon jpegPhoto
        """
        attr = "thumbnailPhoto" if thumbnail else "jpegPhoto"
        job = Job(
            coro=self._wrap_operation(f"get_user_{attr}",
                lambda: self._ad.get_user_attributes(user_dn).get(attr)),
        )
        return self._submit_job(job)

    def set_user_photo(self, user_dn: str, photo_data: bytes, *, is_thumbnail: bool = False) -> str:
        job = Job(
            coro=self._wrap_operation("set_user_photo",
                lambda: self._ad.set_user_photo(user_dn, photo_data, is_thumbnail=is_thumbnail)),
        )
        return self._submit_job(job)

    def delete_user_photo(self, user_dn: str, *, delete_thumbnail: bool = True) -> str:
        job = Job(
            coro=self._wrap_operation("delete_user_photo",
                lambda: self._ad.delete_user_photo(user_dn, delete_thumbnail=delete_thumbnail)),
        )
        return self._submit_job(job)

    # -- Gestion des jobs ------------------------------------------------------
    
    def cancel_job(self, job_id: str) -> bool:
        """Annule un job en cours."""
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.RUNNING:
            job.cancel()
            return True
        return False

    def cancel_all_jobs(self) -> int:
        """Annule tous les jobs en cours. Retourne le nombre annulés."""
        count = 0
        for job in self._jobs.values():
            if job.status == JobStatus.RUNNING:
                job.cancel()
                count += 1
        return count

    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        job = self._jobs.get(job_id)
        return job.status if job else None

    def get_job_result(self, job_id: str) -> Any:
        job = self._jobs.get(job_id)
        return job.result if job else None

    def get_job_error(self, job_id: str) -> Optional[Exception]:
        job = self._jobs.get(job_id)
        return job.error if job else None

    def wait_for_job(self, job_id: str, timeout_ms: int = 30000) -> bool:
        """Attend la fin d'un job (bloquant - pour tests uniquement)."""
        import time
        start = time.time()
        while (time.time() - start) * 1000 < timeout_ms:
            job = self._jobs.get(job_id)
            if job and job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                return True
            time.sleep(0.05)
        return False

    # -- Accès direct à la connexion synchrone (pour compatibilité) -----------
    
    @property
    def sync_connection(self) -> ADConnection:
        """Retourne la connexion synchrone sous-jacente."""
        return self._ad

    def __getattr__(self, name: str) -> Any:
        """Délégation transparente vers la connexion synchrone pour les attributs non trouvés."""
        return getattr(self._ad, name)