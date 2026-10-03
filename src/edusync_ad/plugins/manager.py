"""Gestionnaire de plugins (T1) — Charge, active, désactive les modules."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from PyQt6.QtWidgets import QWidget

from edusync_ad.plugins.base import IModule, ModuleMetadata, ModuleLoadError

logger = logging.getLogger("edusync_ad.plugins.manager")


class PluginManager:
    """Gestionnaire de plugins — charge, active, désactive les modules.
    
    Découvre les modules via entry_points (packaging) ou répertoire local.
    """
    
    ENTRY_POINT_GROUP = "edusync_ad.modules"
    
    def __init__(self):
        self._modules: dict[str, IModule] = {}           # module_id -> instance
        self._metadata: dict[str, ModuleMetadata] = {}   # module_id -> metadata
        self._widgets: dict[str, QWidget] = {}           # module_id -> widget
        self._active: set[str] = set()                   # IDs des modules actifs
        self._context: dict[str, Any] = {}               # Contexte partagé
        self._load_order: list[str] = []                 # Ordre de chargement (pour dependencies)
    
    def set_context(self, **context) -> None:
        """Définit le contexte partagé passé aux modules."""
        self._context = context
        # Notifier les modules déjà chargés
        for module in self._modules.values():
            if hasattr(module, 'on_context_updated'):
                module.on_context_updated(**context)
    
    def discover_modules(self) -> list[ModuleMetadata]:
        """Découvre tous les modules disponibles via entry_points.
        
        Returns:
            Liste des métadonnées des modules découverts
        """
        discovered = []
        
        # Via entry_points (packages installés)
        try:
            eps = entry_points(group=self.ENTRY_POINT_GROUP)
            for ep in eps:
                try:
                    module_class = ep.load()
                    # Instancier temporairement pour récupérer metadata
                    instance = module_class()
                    meta = instance.metadata
                    meta.entry_point = f"{ep.module}:{ep.name}"
                    self._metadata[meta.id] = meta
                    discovered.append(meta)
                    logger.info("Module découvert via entry_point: %s (%s)", meta.name, meta.id)
                except Exception as exc:
                    logger.error("Échec chargement entry_point %s: %s", ep.name, exc)
        except Exception as exc:
            logger.warning("Impossible de lire entry_points: %s", exc)
        
        return discovered
    
    def load_module(self, module_id: str) -> IModule:
        """Charge et initialise un module par son ID.
        
        Args:
            module_id: ID du module à charger
            
        Returns:
            Instance du module chargé
            
        Raises:
            ModuleLoadError: Si le module n'est pas trouvé ou échoue à charger
        """
        if module_id in self._modules:
            return self._modules[module_id]
        
        # Trouver l'entry_point correspondant
        metadata = self._metadata.get(module_id)
        if not metadata:
            # Essayer de découvrir à la volée
            self.discover_modules()
            metadata = self._metadata.get(module_id)
        
        if not metadata:
            raise ModuleLoadError(f"Module introuvable: {module_id}")
        
        # Vérifier les dépendances
        for dep_id in metadata.dependencies:
            if dep_id not in self._modules:
                self.load_module(dep_id)
        
        # Charger via entry_point
        try:
            eps = entry_points(group=self.ENTRY_POINT_GROUP)
            for ep in eps:
                # On compare par le nom du point d'entrée ou l'ID du module
                if ep.name == module_id or ep.name == metadata.id:
                    module_class = ep.load()
                    instance = module_class()
                    break
            else:
                raise ModuleLoadError(f"Entry point non trouvé pour: {module_id}")
        except Exception as exc:
            raise ModuleLoadError(f"Erreur chargement module {module_id}: {exc}") from exc
        
        # Initialiser le module
        try:
            instance.on_load(**self._context)
            self._modules[module_id] = instance
            self._active.add(module_id)
            self._load_order.append(module_id)
            logger.info("Module chargé: %s", module_id)
        except Exception as exc:
            raise ModuleLoadError(f"Erreur initialisation module {module_id}: {exc}") from exc
        
        return instance
    
    def unload_module(self, module_id: str) -> None:
        """Décharge un module."""
        if module_id not in self._modules:
            return
        
        instance = self._modules[module_id]
        try:
            instance.on_unload()
        except Exception as exc:
            logger.error("Erreur déchargement module %s: %s", module_id, exc)
        
        # Nettoyer le widget si existant
        if module_id in self._widgets:
            widget = self._widgets.pop(module_id)
            widget.deleteLater()
        
        self._modules.pop(module_id, None)
        self._active.discard(module_id)
        if module_id in self._load_order:
            self._load_order.remove(module_id)
        
        logger.info("Module déchargé: %s", module_id)
    
    def get_widget(self, module_id: str, parent: QWidget | None = None) -> QWidget | None:
        """Récupère (ou crée) le widget d'un module."""
        if module_id not in self._modules:
            self.load_module(module_id)
        
        if module_id not in self._widgets:
            instance = self._modules[module_id]
            widget = instance.create_widget(parent=parent, **self._context)
            self._widgets[module_id] = widget
        
        return self._widgets.get(module_id)
    
    def activate_module(self, module_id: str) -> None:
        """Active un module (charge s'il ne l'est pas)."""
        if module_id not in self._active:
            self.load_module(module_id)
    
    def deactivate_module(self, module_id: str) -> None:
        """Désactive un module (garde en mémoire mais nettoie widget)."""
        if module_id in self._widgets:
            widget = self._widgets.pop(module_id)
            widget.deleteLater()
        self._active.discard(module_id)
    
    def is_active(self, module_id: str) -> bool:
        return module_id in self._active
    
    def is_loaded(self, module_id: str) -> bool:
        return module_id in self._modules
    
    def get_metadata(self, module_id: str) -> ModuleMetadata | None:
        return self._metadata.get(module_id)
    
    def list_modules(self) -> list[ModuleMetadata]:
        return list(self._metadata.values())
    
    def list_active_modules(self) -> list[ModuleMetadata]:
        return [self._metadata[mid] for mid in self._active if mid in self._metadata]
    
    def reload_all(self) -> None:
        """Recharge tous les modules actifs avec le contexte actuel."""
        active_copy = list(self._active)
        for module_id in active_copy:
            self.unload_module(module_id)
        for module_id in active_copy:
            self.load_module(module_id)
    
    def shutdown(self) -> None:
        """Arrête proprement tous les modules."""
        for module_id in list(self._active):
            self.unload_module(module_id)
        self._modules.clear()
        self._widgets.clear()
        self._active.clear()
        self._load_order.clear()


# Instance globale (singleton pattern pour simplicité)
_plugin_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """Retourne l'instance globale du gestionnaire de plugins."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


def reset_plugin_manager() -> None:
    """Remet à zéro le gestionnaire (pour tests)."""
    global _plugin_manager
    if _plugin_manager:
        _plugin_manager.shutdown()
    _plugin_manager = None