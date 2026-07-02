"""Gestionnaire de logs applicatif unique, partagé entre l'écran de connexion
et le menu « Journal de l'application ». Capture les logs edusync_ad + ldap3
dans un buffer en mémoire, consultable et exportable depuis l'UI."""

from __future__ import annotations

import logging
from collections import deque

from PyQt6.QtCore import QObject, pyqtSignal

from ldap3.utils.log import (
    BASIC,
    EXTENDED,
    set_library_log_activation_level,
    set_library_log_detail_level,
)

_LOG_FORMAT = logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", "%H:%M:%S")
_LOGGER_NAMES = ("edusync_ad", "ldap3")


class _BufferHandler(QObject, logging.Handler):
    line_emitted = pyqtSignal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(_LOG_FORMAT)

    def emit(self, record: logging.LogRecord) -> None:
        self.line_emitted.emit(self.format(record))


class AppLogManager(QObject):
    """Singleton applicatif : capture en continu les logs de connexion et
    d'opérations AD, avec un mode debug bascule-able (détail LDAP étendu)."""

    _instance: "AppLogManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=5000)
        self._handler = _BufferHandler()
        self._handler.line_emitted.connect(self.buffer.append)
        self._debug_enabled = False
        for name in _LOGGER_NAMES:
            logging.getLogger(name).addHandler(self._handler)
        self.set_debug(False)

    @classmethod
    def instance(cls) -> "AppLogManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def line_emitted(self) -> pyqtSignal:
        return self._handler.line_emitted

    def set_debug(self, enabled: bool) -> None:
        self._debug_enabled = enabled
        level = logging.DEBUG if enabled else logging.INFO
        for name in _LOGGER_NAMES:
            logging.getLogger(name).setLevel(level)
        set_library_log_activation_level(level)
        set_library_log_detail_level(EXTENDED if enabled else BASIC)

    def is_debug_enabled(self) -> bool:
        return self._debug_enabled

    def lines(self) -> list[str]:
        return list(self.buffer)

    def clear(self) -> None:
        self.buffer.clear()
