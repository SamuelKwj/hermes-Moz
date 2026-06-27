# Packaging Notes

## PyInstaller

Build one-folder output:

```powershell
.\scripts\build_pyinstaller.ps1
```

Output:

```text
dist\HermesVoice\HermesVoice.exe
```

One-folder is intentional. It keeps WebView, STT, frontend assets, and optional ffmpeg files inspectable during productization.

## Runtime Data

User data is never stored beside the exe. The app writes to:

```text
%LOCALAPPDATA%\HermesVoiceWidget
```

Important subdirectories:

- `logs\app.log`: rotating application log.
- `models\`: faster-whisper and Hugging Face cache.
- `settings.json`: user settings.

## Model Cache Strategy

Whisper models are not bundled into the installer by default. On first use, faster-whisper downloads model files into:

```text
%LOCALAPPDATA%\HermesVoiceWidget\models
```

This keeps the installer smaller and avoids rebuilding the installer when the STT model changes. For offline installs, pre-seed the same folder during deployment.

## ffmpeg Strategy

The app first uses a bundled `bin\ffmpeg.exe` when present, then falls back to PATH. To ship ffmpeg with the app, place binaries here before running PyInstaller:

```text
bin\ffmpeg.exe
bin\ffplay.exe
```

The PyInstaller spec automatically includes `bin\` if it exists.

## Inno Setup

After PyInstaller succeeds, build the installer:

```powershell
.\scripts\build_installer.ps1
```

Output:

```text
dist\installer\HermesVoiceSetup.exe
```
