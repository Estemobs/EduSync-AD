"""Package plugins - Système de modules extensibles (T1)."""

from edusync_ad.plugins.manager import PluginManager, get_plugin_manager, reset_plugin_manager
from edusync_ad.plugins.base import IModule, ModuleMetadata, ModuleLoadError

__all__ = [
    "PluginManager",
    "get_plugin_manager",
    "reset_plugin_manager",
    "IModule",
    "ModuleMetadata",
    "ModuleLoadError",
]