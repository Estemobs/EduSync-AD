# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Build Windows : pyinstaller packaging/edusync_ad.spec

from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC  = ROOT / "src"
ASSETS = ROOT / "assets"
ICON = str(ASSETS / "icon.ico") if (ASSETS / "icon.ico").exists() else None

a = Analysis(
    [str(SRC / "edusync_ad" / "app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(p), "assets")
        for p in (ASSETS / "icon.ico", ASSETS / "icon.png")
        if p.exists()
    ],
    hiddenimports=[
        "ldap3",
        "cryptography",
        "platformdirs",
        "PyQt6.QtCore",
        "PyQt6.QtWidgets",
        "PyQt6.QtGui",
        "reportlab",
        "reportlab.pdfgen",
        "reportlab.lib",
        "reportlab.graphics",
        "reportlab.graphics.shapes",
        "reportlab.graphics.renderPDF",
        "reportlab.graphics.barcode",
        "reportlab.graphics.barcode.qr",
        "reportlab.graphics.barcode.common",
        "reportlab.graphics.barcode.code128",
        "reportlab.graphics.barcode.code93",
        "reportlab.graphics.barcode.code39",
        "reportlab.graphics.barcode.usps",
        "reportlab.graphics.barcode.usps4s",
        "reportlab.graphics.barcode.ecc200datamatrix",
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
    console=False,
    icon=ICON,
    version=str(ROOT / "packaging" / "version_info.txt"),
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
