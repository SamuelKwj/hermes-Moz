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

The app first uses a bundled `bin\ffmpeg.exe` when present, then falls back to PATH. Release builds prepare this local, git-ignored folder from the developer machine's PATH:

```powershell
.\scripts\prepare_release_bin.ps1 -Required
```

That creates:

```text
bin\ffmpeg.exe
bin\ffplay.exe
```

The PyInstaller build script runs the same preparation step before packaging, and the PyInstaller spec automatically includes `bin\` if it exists. The `bin\` directory is intentionally ignored by git because these are large third-party binaries.

Before public sale, verify the license terms of the exact FFmpeg build being bundled and include any required notices.

Release packages include `THIRD_PARTY_NOTICES.md`. Keep that file updated whenever adding a bundled binary, a model, or an installer/runtime dependency with distribution obligations.

References checked on 2026-06-29:

- FFmpeg legal page: https://www.ffmpeg.org/legal.html
- Gyan FFmpeg builds page: https://www.gyan.dev/ffmpeg/builds/
- Inno Setup license text: https://jrsoftware.org/files/is/license.txt
- Inno Setup commercial license page: https://jrsoftware.org/isorder.php

## Inno Setup

After PyInstaller succeeds, build the installer:

```powershell
.\scripts\build_installer.ps1
```

Output:

```text
dist\installer\HermesVoiceSetup.exe
```

## Release Smoke

Run source/runtime checks:

```powershell
.\scripts\release_smoke.ps1
```

Rebuild portable and installer after the same checks:

```powershell
.\scripts\release_smoke.ps1 -Build
```

Smoke-test built artifacts:

```powershell
.\scripts\package_smoke.ps1
.\scripts\package_smoke.ps1 -Installer -UninstallIfIsolated
```

The package smoke script starts the portable exe and, when requested, performs an isolated installer run under `work\install-smoke-*`, verifies `/health`, `/api/status`, bundled `ffmpeg`/`ffplay`, and `THIRD_PARTY_NOTICES.md`, then can uninstall the isolated install.

## Public Release Gate

Run the gate in reporting mode:

```powershell
.\scripts\release_gate.ps1
```

For a public-sale decision, require signed artifacts and external evidence:

```powershell
.\scripts\release_gate.ps1 -RequireSigned -RequireExternalEvidence -EvidencePath .\docs\releases\<candidate>-evidence.json
```

The evidence JSON should contain boolean fields for `clean_windows`, `webview2`, `microphone`, `license_compliance`, `model_terms`, and `privacy_review`. Use `docs\release_evidence_template.md` as the human checklist for collecting that evidence.

## Code Signing

After building the portable app and installer, sign both artifacts with either a PFX file or a certificate already installed in the Windows certificate store:

```powershell
.\scripts\sign_release.ps1 -PfxPath C:\path\to\certificate.pfx -PfxPassword "<password>"
```

or:

```powershell
.\scripts\sign_release.ps1 -CertificateThumbprint "<thumbprint>"
```

The script uses `signtool.exe`, applies SHA-256 signing with a timestamp URL, and verifies the resulting Authenticode signatures for:

```text
dist\HermesVoice\HermesVoice.exe
dist\installer\HermesVoiceSetup.exe
```

Use a trusted code signing certificate for a public release. A self-signed certificate is only useful for testing the mechanics and should not be counted as sale-ready signing evidence.

To verify the current artifacts without signing:

```powershell
.\scripts\sign_release.ps1 -VerifyOnly
```

This command should fail with `NotSigned` until a real certificate has been applied to both artifacts.

After signing, run:

```powershell
.\scripts\release_gate.ps1 -RequireSigned
```
