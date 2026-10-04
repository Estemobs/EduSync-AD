"""EduSync AD — Gestion Active Directory pour établissements scolaires."""

from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:
    __version__ = _pkg_version("edusync-ad")
except PackageNotFoundError:
    __version__ = "1.16.5"  # fallback développement

__all__ = ["__version__"]