"""Package AD - Connexion et opérations Active Directory."""

from edusync_ad.core.ad.connection import (
    ADConnection,
    ConnectResult,
    ConnectionState,
    default_connection_factory,
    is_builtin_group_dn,
)

from edusync_ad.core.ad.async_connection import (
    AsyncADConnection,
    Job,
    JobStatus,
    JobSignals,
)

from edusync_ad.core.ad.exceptions import (
    ADAuthError,
    ADCertificateError,
    ADError,
    ADInsufficientRightsError,
    ADUnreachableError,
)

__all__ = [
    # Connexion synchrone (existant)
    "ADConnection",
    "ConnectResult",
    "ConnectionState",
    "default_connection_factory",
    "is_builtin_group_dn",
    # Connexion asynchrone (T0 - nouveau)
    "AsyncADConnection",
    "Job",
    "JobStatus",
    "JobSignals",
    # Exceptions
    "ADError",
    "ADAuthError",
    "ADUnreachableError",
    "ADInsufficientRightsError",
    "ADCertificateError",
]