"""Modules intégrés (built-in) comme plugins.

Cette implémentation enveloppe les modules UI existants dans l'interface IModule
pour démontrer le système de plugins T1 sans casser l'existant.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QWidget

from edusync_ad.plugins.base import IModule, ModuleMetadata
from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.config import AppConfig
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.core.ad.async_connection import AsyncADConnection


class _BaseModule(IModule):
    """Classe de base pour les modules intégrés."""
    
    def __init__(self):
        self._widget: QWidget | None = None
        self._context: dict[str, Any] = {}
    
    def on_load(self, **context) -> None:
        self._context = context
    
    def on_unload(self) -> None:
        if self._widget:
            self._widget.deleteLater()
            self._widget = None
    
    def on_config_changed(self, config: AppConfig) -> None:
        if hasattr(self._widget, 'update_config'):
            self._widget.update_config(config)
    
    def create_widget(self, parent: QWidget | None = None, **context) -> QWidget:
        # Fusionner le contexte stocké et le contexte passé
        merged_context = {**self._context, **context}
        return self._create_widget_impl(parent, **merged_context)
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        raise NotImplementedError


class CreateAccountsModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="create_accounts",
            name="Création de comptes",
            version="1.0.0",
            description="Import CSV, génération identifiants/mots de passe, création comptes AD",
            requires_ad=True,
            permissions=["create_user", "create_ou", "create_group", "reset_password"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.create_accounts_page import CreateAccountsPage
        
        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        password_vault = context.get("password_vault")
        session_id = context.get("session_id")
        async_ad = context.get("async_ad")
        
        if not all([ad_connection, config, audit_log, password_vault, session_id]):
            raise ValueError("Contexte incomplet pour CreateAccountsModule")
        
        widget = CreateAccountsPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            password_vault=password_vault,
            session_id=session_id,
            async_ad=async_ad,
            parent=parent,
        )
        self._widget = widget
        return widget


class MigrationModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="migration",
            name="Migration de classes",
            version="1.0.0",
            description="Migration d'utilisateurs entre OU/classes via CSV ou interface",
            requires_ad=True,
            permissions=["move_user", "modify_group"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.migration_page import MigrationPage
        
        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        session_id = context.get("session_id")
        
        if not all([ad_connection, config, audit_log, session_id]):
            raise ValueError("Contexte incomplet pour MigrationModule")
        
        widget = MigrationPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget


class DepartModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="depart",
            name="Gestion des départs",
            version="1.0.0",
            description="Désactivation immédiate ou suppression différée avec archivage",
            requires_ad=True,
            permissions=["disable_account", "delete_user", "manage_groups"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.depart_page import DepartPage
        
        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        password_vault = context.get("password_vault")
        session_id = context.get("session_id")
        
        if not all([ad_connection, config, audit_log, password_vault, session_id]):
            raise ValueError("Contexte incomplet pour DepartModule")
        
        widget = DepartPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            password_vault=password_vault,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget


class PasswordResetModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="password_reset",
            name="Réinitialisation mots de passe",
            version="1.0.0",
            description="Réinitialisation en masse par OU, groupe ou CSV",
            requires_ad=True,
            permissions=["reset_password"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.password_reset_page import PasswordResetPage
        
        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        password_vault = context.get("password_vault")
        session_id = context.get("session_id")
        
        if not all([ad_connection, config, audit_log, password_vault, session_id]):
            raise ValueError("Contexte incomplet pour PasswordResetModule")
        
        widget = PasswordResetPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            password_vault=password_vault,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget


class ADExplorerModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="ad_explorer",
            name="Explorateur AD",
            version="1.0.0",
            description="Arborescence OUs + groupes, panneau central unifié, actions clic droit",
            requires_ad=True,
            permissions=["read_ad", "modify_ad"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.ad_explorer_page import ADExplorerPage
        
        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        password_vault = context.get("password_vault")
        session_id = context.get("session_id")
        
        if not all([ad_connection, config, audit_log, password_vault, session_id]):
            raise ValueError("Contexte incomplet pour ADExplorerModule")
        
        widget = ADExplorerPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            password_vault=password_vault,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget


class ExportModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="export",
            name="Export (CSV / Étiquettes PDF)",
            version="1.0.0",
            description="Export CSV sélectif, étiquettes PDF Avery L7160/L7163, QR codes",
            requires_ad=True,
            permissions=["read_ad", "export_data"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.export_page import ExportPage
        
        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        password_vault = context.get("password_vault")
        session_id = context.get("session_id")
        
        if not all([ad_connection, config, audit_log, password_vault, session_id]):
            raise ValueError("Contexte incomplet pour ExportModule")
        
        widget = ExportPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            password_vault=password_vault,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget


class AuditModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="audit",
            name="Journal d'actions",
            version="1.0.0",
            description="Filtres date/type/résultat, export CSV, SQLite local",
            requires_ad=False,
            permissions=["read_audit"],
        )
    
    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.audit_page import AuditPage
        
        audit_log = context.get("audit_log")
        
        if not audit_log:
            raise ValueError("Contexte incomplet pour AuditModule")
        
        widget = AuditPage(audit_log=audit_log, parent=parent)
        self._widget = widget
        return widget


class SettingsModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="settings",
            name="Paramètres globaux",
            version="1.0.0",
            description="Configuration comptes, mots de passe, apparence, connexion LDAPS",
            requires_ad=False,
            permissions=["admin"],
        )

    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.settings_page import SettingsPage

        config = context.get("config")
        on_config_saved = context.get("on_config_saved")
        password_vault = context.get("password_vault")
        ad_domain = context.get("ad_domain")

        if not all([config, on_config_saved]):
            raise ValueError("Contexte incomplet pour SettingsModule")

        widget = SettingsPage(
            config=config,
            on_config_saved=on_config_saved,
            password_vault=password_vault,
            ad_domain=ad_domain,
            parent=parent,
        )
        self._widget = widget
        return widget


class ProfilesModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="profiles",
            name="Profils utilisateurs",
            version="1.0.0",
            description="Profils itinérants / locaux / obligatoires (.man) par OU ou groupe",
            requires_ad=True,
            permissions=["modify_user"],
        )

    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.profiles_page import ProfilesPage

        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        session_id = context.get("session_id")

        if not all([ad_connection, config, audit_log, session_id]):
            raise ValueError("Contexte incomplet pour ProfilesModule")

        widget = ProfilesPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget


class HomeDirsModule(_BaseModule):
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata(
            id="homedirs",
            name="Dossiers personnels",
            version="1.0.0",
            description="Home Directory (homeDirectory/homeDrive) + droits NTFS, script PowerShell",
            requires_ad=True,
            permissions=["modify_user"],
        )

    def _create_widget_impl(self, parent: QWidget | None, **context) -> QWidget:
        from edusync_ad.ui.modules.home_dirs_page import HomeDirsPage

        ad_connection = context.get("ad_connection")
        config = context.get("config")
        audit_log = context.get("audit_log")
        session_id = context.get("session_id")

        if not all([ad_connection, config, audit_log, session_id]):
            raise ValueError("Contexte incomplet pour HomeDirsModule")

        widget = HomeDirsPage(
            ad_connection=ad_connection,
            config=config,
            audit_log=audit_log,
            session_id=session_id,
            parent=parent,
        )
        self._widget = widget
        return widget