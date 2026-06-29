# Productization Status

## Implemented

- Settings panel in the desktop UI.
- First-run diagnostics wizard.
- Runtime diagnostics endpoints for dependencies, devices, Hermes, logs, settings, and model cache.
- System tray show/hide/exit menu.
- Rotating log file under `%LOCALAPPDATA%\HermesVoiceWidget\logs`.
- faster-whisper model cache under `%LOCALAPPDATA%\HermesVoiceWidget\models`.
- Optional bundled `bin\ffmpeg.exe` / `bin\ffplay.exe` strategy.
- Release bin preparation script that copies `ffmpeg.exe` and `ffplay.exe` from PATH into ignored local `bin\`.
- Release smoke script for compile, frontend syntax, dependency imports, unit tests, backend readiness, and optional builds.
- PyInstaller one-folder spec and build script.
- Inno Setup installer script and build script.

## Verified

- 2026-06-29: `.\scripts\release_smoke.ps1`
  - Python source compilation: `python -m compileall -q launcher.py backend`
  - Frontend inline JavaScript syntax parse: `checked 2 script block(s)`
  - Dependency imports: `fastapi`, `uvicorn`, `webview`, `pystray`, `PyInstaller`, `faster_whisper`, `edge_tts`, `sounddevice`, `numpy`, `httpx`
  - Unit tests: 5 passed
  - Backend readiness on a temporary port: `/health` ok, `/api/status` reports `stt_ready=true` and `tts_ready=true`
- 2026-06-29: `.\scripts\release_smoke.ps1 -Build`
  - Prepared bundled `bin\ffmpeg.exe` and `bin\ffplay.exe` from PATH.
  - PyInstaller one-folder build succeeded.
  - Inno Setup build succeeded.
- 2026-06-29 current artifacts:
  - `dist\HermesVoice\HermesVoice.exe`, 15,806,386 bytes, `2026-06-29 22:34:44`
  - `dist\installer\HermesVoiceSetup.exe`, 191,598,759 bytes, `2026-06-29 22:40:50`
- 2026-06-29 packaged exe smoke test:
  - Launched `dist\HermesVoice\HermesVoice.exe` on a temporary port.
  - `/health` ok, `/api/status` reports `stt_ready=true`, `tts_ready=true`, bundled `ffmpeg=true`, bundled `ffplay=true`.
- 2026-06-29 installed app smoke test:
  - Silent installer run completed.
  - Existing machine install record reused `%LOCALAPPDATA%\Programs\Hermes Voice`.
  - Launched installed exe on a temporary port.
  - `/health` ok, `/api/status` reports `stt_ready=true`, `tts_ready=true`, bundled `ffmpeg=true`, bundled `ffplay=true`.

## Not Yet Verified

- Clean Windows install and uninstall flow.
- WebView2 availability on a clean target machine.
- Microphone permission behavior on first launch.
- Offline model pre-seeding.
- Isolated installer install/uninstall on a machine with no previous Hermes Voice installation record.
- Public-sale license compliance for bundled FFmpeg build and installer toolchain output. As of 2026-06-29, FFmpeg's legal page and Gyan's builds page need review before shipping the bundled binaries commercially; Inno Setup's license permits commercial use, while the project requests commercial users purchase a license.
- Code signing and reputation behavior on a clean Windows target.
- End-to-end voice interaction after install with a real microphone and speaker device.

## Notes

Inno Setup was installed with winget and found at:

```text
%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
```

The generated installer uses the built-in English Inno Setup language file because the winget Inno package does not include `ChineseSimplified.isl`.

The 2026-06-29 installer smoke used the existing installed location because Inno Setup reused the previous AppId install record on this machine. Do a fresh VM test before treating the installer/uninstaller flow as sale-ready.

Rebuild commands:

```powershell
.\scripts\setup.ps1
.\scripts\release_smoke.ps1
.\scripts\build_pyinstaller.ps1
.\scripts\build_installer.ps1
```
