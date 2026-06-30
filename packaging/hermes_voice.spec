# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "frontend"), "frontend"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]

if (ROOT / "bin").exists():
    datas.append((str(ROOT / "bin"), "bin"))

hiddenimports = [
    "app_runtime",
    "hermes_client",
    "server",
    "settings",
    "stt_engine",
    "system_checks",
    "tts_engine",
    "voice_pipeline",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "webview.platforms.edgechromium",
]

for package in ("edge_tts", "faster_whisper", "pystray"):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT), str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HermesVoice",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icons" / "app-icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="HermesVoice",
)
