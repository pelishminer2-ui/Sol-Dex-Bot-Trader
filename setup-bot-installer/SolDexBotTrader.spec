# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Sol Dex Bot Trader Windows app."""

import sys
from pathlib import Path

# SPECPATH is the directory containing this .spec (setup-bot-installer/).
# Repo root is the parent — always package live dashboard/static/config from there
# (no stale copies under setup-bot-installer/).
SPECDIR = Path(SPECPATH).resolve()
ROOT = SPECDIR.parent
assert (ROOT / "static" / "index.html").is_file(), (
    f"Expected dashboard at {ROOT / 'static' / 'index.html'}"
)
assert (ROOT / "config.py").is_file(), f"Expected config at {ROOT / 'config.py'}"

block_cipher = None

hiddenimports = [
    "flask",
    "flask_cors",
    "dotenv",
    "base58",
    "solders",
    "solders.keypair",
    "solders.pubkey",
    "solders.system_program",
    "solders.transaction",
    "solders.signature",
    "solana",
    "solana.rpc.async_api",
    "solana.rpc.commitment",
    "solana.rpc.models",
    "aiohttp",
    "openpyxl",
    "requests",
    "live_start_fee",
    "bot_manager",
    "bot",
    "config",
    "security_firewall",
    "paper_session",
    "pnl_tracker",
    "tax_export",
    "live_tradeable_balance",
]

datas = [
    # Live copies from repo root on every build.bat / build.ps1 run.
    (str(ROOT / "static"), "static"),
    (str(ROOT / "presets"), "presets"),
    (str(ROOT / ".env.example"), "."),
    (str(SPECDIR / "Sol-Dex-Bot-Trader-User-Guide.pdf"), "docs"),
    (str(SPECDIR / "version.txt"), "."),
    (str(SPECDIR / "BUILD_INFO.txt"), "."),
]

# Include optional branding assets if present
assets = ROOT / "static" / "assets"
if assets.is_dir():
    datas.append((str(assets), "static/assets"))

a = Analysis(
    [str(SPECDIR / "run_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SolDexBotTrader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SolDexBotTrader",
)
