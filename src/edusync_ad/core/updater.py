"""Vérification et téléchargement des mises à jour depuis les releases GitHub."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

RELEASES_API_URL = "https://api.github.com/repos/estemobs/EduSync-AD/releases/latest"
CURRENT_VERSION = "1.2.0"


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


def download_and_install(download_url: str, progress_callback=None) -> bool:
    """Télécharge et lance l'installateur Windows. Retourne True si succès."""
    if platform.system() != "Windows":
        return False

    tmp_dir = Path(tempfile.mkdtemp(prefix="edusync_update_"))
    installer_path = tmp_dir / "EduSyncAD-Setup.exe"

    try:
        def _reporthook(count, block_size, total_size):
            if progress_callback and total_size > 0:
                pct = min(100, int(count * block_size * 100 / total_size))
                progress_callback(pct)

        request = Request(download_url, headers={"User-Agent": "EduSync-AD-Updater"})
        with urlopen(request, timeout=30) as resp, open(installer_path, "wb") as out:
            total_size = int(resp.headers.get("Content-Length", 0))
            block_size = 65536
            count = 0
            while chunk := resp.read(block_size):
                out.write(chunk)
                count += 1
                _reporthook(count, block_size, total_size)

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
