"""Vérification et téléchargement des mises à jour depuis les releases GitHub."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

logger = logging.getLogger("edusync_ad.updater")

RELEASES_API_URL = "https://api.github.com/repos/estemobs/EduSync-AD/releases/latest"
CURRENT_VERSION = "1.5.0"


def _parse_version(v: str) -> tuple[int, ...]:
    v = v.lstrip("v")
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def check_for_update(timeout: int = 8) -> dict | None:
    """Retourne les infos de la release si une version plus récente existe, sinon None."""
    request = Request(
        RELEASES_API_URL,
        headers={
            "User-Agent": "EduSync-AD-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, OSError, json.JSONDecodeError):
        return None

    latest_tag = data.get("tag_name", "")
    if not latest_tag:
        return None

    if _parse_version(latest_tag) <= _parse_version(CURRENT_VERSION):
        return None

    assets = data.get("assets", [])
    is_windows = platform.system() == "Windows"
    suffix = "-Setup.exe" if is_windows else "-linux.flatpak"
    download_url = next(
        (a["browser_download_url"] for a in assets if a["name"].endswith(suffix)),
        None,
    )

    return {
        "version": latest_tag,
        "current": CURRENT_VERSION,
        "download_url": download_url,
        "release_notes": data.get("body", ""),
    }


def _download_to(url: str, dest: Path, progress_callback=None) -> None:
    request = Request(url, headers={"User-Agent": "EduSync-AD-Updater"})
    with urlopen(request, timeout=30) as resp, open(dest, "wb") as out:
        total_size = int(resp.headers.get("Content-Length", 0))
        block_size = 65536
        count = 0
        while chunk := resp.read(block_size):
            out.write(chunk)
            count += 1
            if progress_callback and total_size > 0:
                pct = min(100, int(count * block_size * 100 / total_size))
                progress_callback(pct)


def _install_windows(download_url: str, progress_callback=None) -> bool:
    tmp_dir = Path(tempfile.mkdtemp(prefix="edusync_update_"))
    installer_path = tmp_dir / "EduSyncAD-Setup.exe"
    try:
        _download_to(download_url, installer_path, progress_callback)

        # Laisse le process courant se terminer (l'UI appelle sys.exit juste après)
        # avant de lancer l'installateur, qui remplace EduSyncAD.exe puis le relance.
        bat = tmp_dir / "run_installer.bat"
        bat.write_text(
            f"@echo off\n"
            f"timeout /t 2 /nobreak > nul\n"
            f'"{installer_path}" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES\n'
            f'rmdir /S /Q "{tmp_dir}"\n',
            encoding="utf-8",
        )
        subprocess.Popen(["cmd.exe", "/c", str(bat)], creationflags=0x08000000)
        return True
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False


def _run_logged(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    logger.info("Exécution : %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode == 0:
        logger.info("OK (code 0)%s", f" — {result.stdout.strip()}" if result.stdout.strip() else "")
    else:
        logger.warning(
            "Échec (code %s) — stdout: %s — stderr: %s",
            result.returncode, result.stdout.strip(), result.stderr.strip(),
        )
    return result


def _install_linux_flatpak(download_url: str, progress_callback=None) -> bool:
    tmp_dir = Path(tempfile.mkdtemp(prefix="edusync_update_"))
    bundle_path = tmp_dir / "EduSyncAD-linux.flatpak"
    try:
        _download_to(download_url, bundle_path, progress_callback)

        # Depuis le bac à sable Flatpak, "flatpak-spawn --host" exécute la
        # commande sur l'hôte (nécessite --talk-name=org.freedesktop.Flatpak
        # dans le manifeste). Hors sandbox (dev), on appelle flatpak directement.
        host_prefix = ["flatpak-spawn", "--host"] if shutil.which("flatpak-spawn") else []

        # --or-update : sans ce flag, "flatpak install" échoue avec "already
        # installed" si une tentative précédente (même partielle) a déjà posé
        # une copie à cette échelle — ce qui arrive à chaque nouvel essai.
        user_cmd = host_prefix + [
            "flatpak", "install", "--user", "--noninteractive", "--or-update", "-y", str(bundle_path),
        ]
        system_cmd = host_prefix + [
            "pkexec", "flatpak", "install", "--system", "--noninteractive", "--or-update", "-y", str(bundle_path),
        ]

        # Tentative en --user d'abord : aucune élévation de privilèges requise.
        result = _run_logged(user_cmd, timeout=120)
        if result.returncode == 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return True

        # Repli : l'app est peut-être installée en --system (nécessite les
        # droits root). pkexec affiche sa propre fenêtre d'autorisation
        # native (comme l'UAC Windows) — ce n'est pas une élévation silencieuse.
        result = _run_logged(system_cmd, timeout=180)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return result.returncode == 0
    except Exception as exc:
        logger.warning("Échec de la mise à jour Flatpak : %s", exc)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False


def download_and_install(download_url: str, progress_callback=None) -> bool:
    """Télécharge et installe la mise à jour. Retourne True si succès."""
    system = platform.system()
    if system == "Windows":
        return _install_windows(download_url, progress_callback)
    if system == "Linux":
        return _install_linux_flatpak(download_url, progress_callback)
    return False
