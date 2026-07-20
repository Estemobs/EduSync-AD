"""Tests de la vérification d'intégrité (SHA-256) de la mise à jour automatique
(core/updater.py) — le téléchargement d'un binaire exécuté ensuite en silencieux
justifie de vérifier son intégrité avant installation (voir _verify_checksum)."""

from __future__ import annotations

import hashlib
import io
import json

from edusync_ad.core.updater import (
    _fetch_expected_checksum,
    _install_linux_flatpak,
    _sha256_of_file,
    _verify_checksum,
    check_for_update,
)


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_sha256_of_file_matches_hashlib(tmp_path):
    path = tmp_path / "fichier.bin"
    content = b"contenu quelconque" * 1000
    path.write_bytes(content)
    assert _sha256_of_file(path) == hashlib.sha256(content).hexdigest()


def test_fetch_expected_checksum_parses_plain_hash(mocker):
    fake_hash = "a" * 64
    mocker.patch(
        "edusync_ad.core.updater.urlopen",
        return_value=_FakeResponse(fake_hash.encode("ascii")),
    )
    assert _fetch_expected_checksum("https://example.invalid/f.sha256") == fake_hash


def test_fetch_expected_checksum_parses_sha256sum_format(mocker):
    fake_hash = "b" * 64
    mocker.patch(
        "edusync_ad.core.updater.urlopen",
        return_value=_FakeResponse(f"{fake_hash}  EduSyncAD-Setup.exe\n".encode("ascii")),
    )
    assert _fetch_expected_checksum("https://example.invalid/f.sha256") == fake_hash


def test_verify_checksum_true_when_hash_matches(tmp_path, mocker):
    path = tmp_path / "installer.exe"
    path.write_bytes(b"binaire simule")
    expected = hashlib.sha256(b"binaire simule").hexdigest()
    mocker.patch("edusync_ad.core.updater._fetch_expected_checksum", return_value=expected)
    assert _verify_checksum(path, "https://example.invalid/f.sha256") is True


def test_verify_checksum_false_when_hash_differs(tmp_path, mocker):
    path = tmp_path / "installer.exe"
    path.write_bytes(b"binaire modifie")
    mocker.patch("edusync_ad.core.updater._fetch_expected_checksum", return_value="0" * 64)
    assert _verify_checksum(path, "https://example.invalid/f.sha256") is False


def test_verify_checksum_permissive_without_checksum_url(tmp_path):
    path = tmp_path / "installer.exe"
    path.write_bytes(b"binaire")
    assert _verify_checksum(path, None) is True


def test_verify_checksum_permissive_when_checksum_unavailable(tmp_path, mocker):
    path = tmp_path / "installer.exe"
    path.write_bytes(b"binaire")
    mocker.patch("edusync_ad.core.updater._fetch_expected_checksum", return_value=None)
    assert _verify_checksum(path, "https://example.invalid/f.sha256") is True


def test_check_for_update_includes_checksum_url(mocker):
    payload = {
        "tag_name": "v99.0.0",
        "body": "Notes",
        "assets": [
            {"name": "EduSyncAD-Setup.exe", "browser_download_url": "https://x/EduSyncAD-Setup.exe"},
            {
                "name": "EduSyncAD-Setup.exe.sha256",
                "browser_download_url": "https://x/EduSyncAD-Setup.exe.sha256",
            },
        ],
    }
    mocker.patch("edusync_ad.core.updater.platform.system", return_value="Windows")
    mocker.patch(
        "edusync_ad.core.updater.urlopen",
        return_value=_FakeResponse(json.dumps(payload).encode("utf-8")),
    )
    info = check_for_update()
    assert info is not None
    assert info["download_url"] == "https://x/EduSyncAD-Setup.exe"
    assert info["checksum_url"] == "https://x/EduSyncAD-Setup.exe.sha256"


def test_install_linux_flatpak_downloads_under_user_cache_not_system_tmp(tmp_path, mocker):
    """Régression : le paquet doit être téléchargé sous le dossier de cache
    utilisateur (partagé avec l'hôte via --filesystem=home), jamais sous /tmp
    — invisible depuis l'hôte quand la commande passe par flatpak-spawn
    --host, ce qui faisait échouer l'installation avec "no such file"."""
    fake_cache = tmp_path / "cache"
    mocker.patch("edusync_ad.core.updater.user_cache_dir", return_value=str(fake_cache))
    downloaded_paths = []

    def fake_download(url, dest, progress_callback=None):
        downloaded_paths.append(dest)
        dest.write_bytes(b"fake bundle")

    mocker.patch("edusync_ad.core.updater._download_to", side_effect=fake_download)
    mocker.patch("edusync_ad.core.updater.shutil.which", return_value=None)
    fake_result = mocker.Mock(returncode=0, stdout="", stderr="")
    mocker.patch("edusync_ad.core.updater.subprocess.run", return_value=fake_result)

    ok, finalize = _install_linux_flatpak("https://example.invalid/EduSyncAD-linux.flatpak")

    assert ok is True
    assert len(downloaded_paths) == 1
    assert str(downloaded_paths[0]).startswith(str(fake_cache))
    # La relance ne doit pas avoir eu lieu pendant l'installation elle-même —
    # seulement quand l'appelant invoque explicitement `finalize` (après
    # confirmation de l'utilisateur, voir update_dialog.py).
    assert finalize is not None
    fake_popen = mocker.patch("edusync_ad.core.updater.subprocess.Popen")
    finalize()
    fake_popen.assert_called_once()


def test_check_for_update_checksum_url_none_when_asset_missing(mocker):
    payload = {
        "tag_name": "v99.0.0",
        "body": "",
        "assets": [
            {"name": "EduSyncAD-Setup.exe", "browser_download_url": "https://x/EduSyncAD-Setup.exe"},
        ],
    }
    mocker.patch("edusync_ad.core.updater.platform.system", return_value="Windows")
    mocker.patch(
        "edusync_ad.core.updater.urlopen",
        return_value=_FakeResponse(json.dumps(payload).encode("utf-8")),
    )
    info = check_for_update()
    assert info is not None
    assert info["checksum_url"] is None
