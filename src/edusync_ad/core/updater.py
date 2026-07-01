"""Vérification et téléchargement des mises à jour depuis les releases Gitea/GitHub."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, urlretrieve
from urllib.error import URLError
import json

RELEASES_API_URL = "http://192.168.1.109:3000/api/v1/repos/estemobs/EduSync-AD/releases/latest"
CURRENT_VERSION = "1.0.4"


def _parse_version(v: str) -> tuple[int, ...]:
    v = v.lstrip("v")
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def check_for_update(timeout: int = 8) -> dict | None:
    """Retourne les infos de la release si une version plus récente existe, sinon None."""
    try:
        with urlopen(RELEASES_API_URL, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, Exception):
        return None

    latest_tag = data.get("tag_name", "")
    if not latest_tag:
        return None

    if _parse_version(latest_tag) <= _parse_version(CURRENT_VERSION):
        return None

    assets = data.get("assets", [])
    is_windows = platform.system() == "Windows"
    suffix = "-windows.zip" if is_windows else "-linux.flatpak"
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
    """Télécharge et installe la mise à jour. Retourne True si succès."""
    if platform.system() != "Windows":
        return False

    tmp_dir = Path(tempfile.mkdtemp(prefix="edusync_update_"))
    zip_path = tmp_dir / "update.zip"

    try:
        def _reporthook(count, block_size, total_size):
            if progress_callback and total_size > 0:
                pct = min(100, int(count * block_size * 100 / total_size))
                progress_callback(pct)

        urlretrieve(download_url, zip_path, reporthook=_reporthook)

        extract_dir = tmp_dir / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        app_dir = Path(sys.executable).parent

        bat = tmp_dir / "apply_update.bat"
        bat.write_text(
            f"@echo off\n"
            f"timeout /t 2 /nobreak > nul\n"
            f'xcopy /E /Y /I "{extract_dir}\\*" "{app_dir}\\"\n'
            f'start "" "{app_dir}\\EduSyncAD.exe"\n'
            f'rmdir /S /Q "{tmp_dir}"\n',
            encoding="utf-8",
        )
        subprocess.Popen(["cmd.exe", "/c", str(bat)], creationflags=0x08000000)
        return True

    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False
