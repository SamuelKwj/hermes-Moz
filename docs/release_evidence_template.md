# Release Evidence Template

Copy this file for each public release candidate and store the filled copy outside `dist/`, for example:

```text
docs/releases/2026-06-30-rc1-evidence.md
```

The public release gate can consume a completed evidence JSON file. Keep this Markdown file as the human checklist and attach screenshots/logs where useful.

## Candidate

- Version:
- Commit:
- Portable artifact:
- Installer artifact:
- Test machine:
- Tester:
- Date:

## Automated Checks

- `.\scripts\release_smoke.ps1`:
- `.\scripts\release_smoke.ps1 -Build`:
- `.\scripts\package_smoke.ps1 -Installer -UninstallIfIsolated`:
- `.\scripts\release_gate.ps1 -RequireSigned -RequireExternalEvidence -EvidencePath <path>`:

## Clean Windows

- Clean Windows install completed:
- Windows version:
- Fresh user profile:
- Previous Hermes Voice install absent:
- Installer run:
- Start menu shortcut:
- Optional desktop shortcut:
- Uninstall completed:
- No app files left in install directory after uninstall:

## WebView2

- WebView2 runtime present or installed:
- Main UI opened:
- Settings opened:
- Tray menu shown:

## Microphone

- Input device:
- Output device:
- Manual recording succeeded:
- STT transcript:
- LLM reply:
- TTS playback heard:
- Interrupt/stop behavior checked:

## License

- FFmpeg build source:
- FFmpeg license reviewed:
- Required FFmpeg notices/source offer prepared:
- Inno Setup terms reviewed:
- Python/package notices reviewed:
- Model/service terms reviewed:

## Code signing

- `dist\HermesVoice\HermesVoice.exe` signed:
- `dist\installer\HermesVoiceSetup.exe` signed:
- Certificate subject:
- Timestamp server:
- Windows SmartScreen/reputation notes:

## Decision

- Internal release:
- Public sale:
- Remaining blockers:
