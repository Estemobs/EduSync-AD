"""Core package - Modules cœur d'EduSync AD."""

from edusync_ad.core.ad import (
    ADConnection,
    AsyncADConnection,
    ConnectResult,
    ConnectionState,
    Job,
    JobSignals,
    JobStatus,
    default_connection_factory,
    is_builtin_group_dn,
    ADError,
    ADAuthError,
    ADUnreachableError,
    ADInsufficientRightsError,
    ADCertificateError,
    # Cache (T2)
    ADCache,
    CacheStats,
    create_ad_cache,
)

from edusync_ad.plugins import (
    IModule,
    ModuleMetadata,
    PluginManager,
    ModuleLoadError,
    get_plugin_manager,
    reset_plugin_manager,
)

from edusync_ad.core.audit import AuditLog, new_session_id
from edusync_ad.core.config import AppConfig, load_config, save_config
from edusync_ad.core.csv_io import (
    CsvPreview,
    EXPECTED_COLUMNS,
    REQUIRED_COLUMNS,
    export_created_accounts,
    export_failed_rows,
    load_preview,
    load_rows,
)
from edusync_ad.core.identifiers import (
    CAMEL_PRESETS,
    PRESETS,
    IdentifierEngine,
    apply_prenom_compose_rule,
    clean_token,
    render_template,
)
from edusync_ad.core.models import (
    AccountType,
    GeneratedUser,
    MigrationRow,
    PasswordPolicy,
    RawUserRow,
)
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.core.passwords import (
    generate_password,
    generate_passwords_for_batch,
    PasswordPolicy,  # type: ignore[attr-defined]
)
from edusync_ad.core.crypto import (
    decrypt_str as decrypt,
    encrypt_str as encrypt,
    get_or_create_key,
    load_remembered_connection,
    save_remembered_connection,
    clear_remembered_connection,
)
from edusync_ad.core.updater import CURRENT_VERSION, check_for_update
from edusync_ad.core.export import build_export_row, export_users_csv, generate_labels_pdf
from edusync_ad.core.photos import (
    PhotoInfo,
    PhotoError,
    process_photo,
    process_photo_from_path,
    PhotoMapping,
    map_photos_convention,
    map_photos_from_csv,
    create_photo_preview_pixmap,
    photo_to_qicon,
)
from edusync_ad.core.profiles import (
    ProfileType,
    ProfileConfig,
    ProfileTemplate,
    ProfileManager,
    load_all_profile_configs,
    save_all_profile_configs,
)
from edusync_ad.core.homedirs import (
    HomeDirConfig,
    HomeDirManager,
    HomeDirPlan,
    load_home_dir_config,
    save_home_dir_config,
)
from edusync_ad.core.quotas import (
    QuotaSettings,
    QuotaPlan,
    QuotaManager,
    load_all_quota_configs,
    save_all_quota_configs,
)
from edusync_ad.core.logon_hours import (
    DISPLAY_DAYS,
    Grid,
    LogonHoursManager,
    LogonHoursPreset,
    current_utc_offset_hours,
    decode_logon_hours,
    encode_logon_hours,
    grid_summary,
    is_empty,
    is_unrestricted,
    new_grid,
)
from edusync_ad.core.logon_scripts import (
    SCRIPT_VARIABLES,
    LogonScript,
    LogonScriptManager,
    LogonScriptTemplate,
    ScriptPlan,
    build_context,
    deploy_scripts,
    load_logon_scripts,
    netlogon_path,
    render_script,
    save_logon_scripts,
    unknown_variables,
)

__all__ = [
    # AD
    "ADConnection",
    "AsyncADConnection",
    "ConnectResult",
    "ConnectionState",
    "Job",
    "JobSignals",
    "JobStatus",
    "default_connection_factory",
    "is_builtin_group_dn",
    "ADError",
    "ADAuthError",
    "ADUnreachableError",
    "ADInsufficientRightsError",
    "ADCertificateError",
    # Cache (T2)
    "ADCache",
    "CacheStats",
    "create_ad_cache",
    # Plugins (T1)
    "IModule",
    "ModuleMetadata",
    "PluginManager",
    "ModuleLoadError",
    "get_plugin_manager",
    "reset_plugin_manager",
    # Audit
    "AuditLog",
    "new_session_id",
    # Config
    "AppConfig",
    "load_config",
    "save_config",
    # CSV
    "CsvPreview",
    "EXPECTED_COLUMNS",
    "REQUIRED_COLUMNS",
    "export_created_accounts",
    "export_failed_rows",
    "load_preview",
    "load_rows",
    # Identifiers
    "CAMEL_PRESETS",
    "PRESETS",
    "IdentifierEngine",
    "apply_prenom_compose_rule",
    "clean_token",
    "render_template",
    # Models
    "AccountType",
    "GeneratedUser",
    "MigrationRow",
    "PasswordPolicy",
    "RawUserRow",
    # Password vault
    "PasswordVault",
    # Passwords
    "generate_password",
    "generate_passwords_for_batch",
    "PasswordPolicy",
    # Crypto
    "decrypt",
    "encrypt",
    "generate_key",
    "get_or_create_key",
    "load_remembered_connection",
    "save_remembered_connection",
    "clear_remembered_connection",
    # Updater
    "CURRENT_VERSION",
    "check_for_update",
    # Export
    "build_export_row",
    "export_users_csv",
    "generate_labels_pdf",
    # Photos (M12)
    "PhotoInfo",
    "PhotoError",
    "process_photo",
    "process_photo_from_path",
    "PhotoMapping",
    "map_photos_convention",
    "map_photos_from_csv",
    "create_photo_preview_pixmap",
    "photo_to_qicon",
    # Profils (M13)
    "ProfileType",
    "ProfileConfig",
    "ProfileTemplate",
    "ProfileManager",
    "load_all_profile_configs",
    "save_all_profile_configs",
    # Dossiers personnels (M17)
    "HomeDirConfig",
    "HomeDirManager",
    "HomeDirPlan",
    "load_home_dir_config",
    "save_home_dir_config",
    # Quotas FSRM (M14)
    "QuotaSettings",
    "QuotaPlan",
    "QuotaManager",
    "load_all_quota_configs",
    "save_all_quota_configs",
    # Heures de connexion (M16)
    "DISPLAY_DAYS",
    "Grid",
    "LogonHoursManager",
    "LogonHoursPreset",
    "current_utc_offset_hours",
    "decode_logon_hours",
    "encode_logon_hours",
    "grid_summary",
    "is_empty",
    "is_unrestricted",
    "new_grid",
    # Scripts logon/logoff (M15)
    "SCRIPT_VARIABLES",
    "LogonScript",
    "LogonScriptManager",
    "LogonScriptTemplate",
    "ScriptPlan",
    "build_context",
    "deploy_scripts",
    "load_logon_scripts",
    "netlogon_path",
    "render_script",
    "save_logon_scripts",
    "unknown_variables",
]