class ADError(Exception):
    """Erreur de base pour toutes les erreurs liées à l'Active Directory."""


class ADAuthError(ADError):
    """Mauvais mot de passe ou identifiant invalide."""


class ADUnreachableError(ADError):
    """Le contrôleur de domaine est injoignable."""


class ADInsufficientRightsError(ADError):
    """Le compte utilisé n'a pas les droits nécessaires pour l'opération."""


class ADCertificateError(ADError):
    """Certificat SSL invalide lors d'une tentative de connexion LDAPS."""
