# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Build Windows : pyinstaller build/edusync_ad.spec
# Génère dist/EduSyncAD/EduSyncAD.exe (mode onedir, Python embarqué)

from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC  = ROOT / "src"

a = Analysis(
    [str(SRC / "edusync_ad" / "app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "ldap3",
        "cryptography",
        "platformdirs",
        "PyQt6.QtCore",
        "PyQt6.QtWidgets",
        "PyQt6.QtGui",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EduSyncAD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # pas de fenêtre console
    icon=None,       # remplacer par le chemin vers l'icône .ico si disponible
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="EduSyncAD",
)
