"""Base du système de plugins (T1) - Interface IModule et métadonnées."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtWidgets import QWidget


@dataclass
class ModuleMetadata:
    """Métadonnées d'un module plugin."""
    id: str                    # Identifiant unique (ex: "edusync.modules.photos")
    name: str                  # Nom affiché (ex: "Gestion des photos")
    version: str               # Version semver (ex: "1.0.0")
    description: str = ""      # Description courte
    author: str = ""           # Auteur
    requires_ad: bool = True   # Nécessite une connexion AD active
    permissions: list[str] = field(default_factory=list)  # Permissions requises
    dependencies: list[str] = field(default_factory=list)  # IDs de modules requis
    entry_point: str = ""      # Point d'entrée (pour debug)


class IModule(ABC):
    """Interface que tout module plugin doit implémenter."""
    
    @property
    @abstractmethod
    def metadata(self) -> ModuleMetadata:
        """Retourne les métadonnées du module."""
        pass
    
    @abstractmethod
    def create_widget(self, parent: QWidget | None = None, **context) -> QWidget:
        """Crée et retourne le widget principal du module.
        
        Args:
            parent: Widget parent Qt
            **context: Contexte d'exécution (ad_connection, config, audit_log, etc.)
        
        Returns:
            QWidget prêt à être ajouté à l'interface
        """
        pass
    
    def on_load(self, **context) -> None:
        """Appelé quand le module est chargé/activé.
        
        Args:
            **context: Contexte partagé (ad_connection, config, audit_log, password_vault, session_id, async_ad)
        """
        pass
    
    def on_unload(self) -> None:
        """Appelé quand le module est déchargé/désactivé."""
        pass
    
    def on_config_changed(self, config: Any) -> None:
        """Appelé quand la configuration globale change."""
        pass


class ModuleLoadError(Exception):
    """Erreur lors du chargement d'un module."""
    pass