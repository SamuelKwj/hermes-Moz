# Productization Status

## Implemented

- Settings panel in the desktop UI.
- First-run diagnostics wizard.
- Runtime diagnostics endpoints for dependencies, devices, Hermes, logs, settings, and model cache.
- System tray show/hide/exit menu.
- Rotating log file under `%LOCALAPPDATA%\HermesVoiceWidget\logs`.
- faster-whisper model cache under `%LOCALAPPDATA%\HermesVoiceWidget\models`.
- Optional bundled `bin\ffmpeg.exe` / `bin\ffplay.exe` strategy.
- PyInstaller one-folder spec and build script.
- Inno Setup installer script and build script.

## Verified

- Python source compilation: `python -m compileall launcher.py backend`
- Runtime helper import and directory creation.
- Frontend inline JavaScript syntax: `node --check`
- Git whitespace check: `git diff --check`
- Dependency installation with `uv pip install --python .\.venv\Scripts\python.exe -r backend\requirements.txt`.
- PyInstaller one-folder build: `dist\HermesVoice\HermesVoice.exe`.
- Packaged exe smoke test: launched on a temporary port and returned `/health`.
- Inno Setup build: `dist\installer\HermesVoiceSetup.exe`.
- Installer smoke test: silent install to `work\install-test`, launched installed exe on a temporary port, returned `/health`, then silent uninstall.

## Not Yet Verified

- Clean Windows install and uninstall flow.
- WebView2 availability on a clean target machine.
- Microphone permission behavior on first launch.
- Offline model pre-seeding.

## Notes

Inno Setup was installed with winget and found at:

```text
%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
```

The generated installer uses the built-in English Inno Setup language file because the winget Inno package does not include `ChineseSimplified.isl`.

Rebuild commands:

```powershell
.\scripts\setup.ps1
.\scripts\build_pyinstaller.ps1
.\scripts\build_installer.ps1
```
