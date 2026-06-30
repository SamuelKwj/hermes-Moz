# Third-Party Notices

Hermes Voice bundles or relies on third-party components. This file is included with release packages so users can inspect the main runtime dependencies and the links that must be reviewed before public distribution.

## FFmpeg

Release builds can bundle `ffmpeg.exe` and `ffplay.exe` in the app's `bin` folder. The current build preparation script copies those binaries from the developer machine's PATH.

Important references:

- FFmpeg legal page: https://www.ffmpeg.org/legal.html
- Gyan FFmpeg builds: https://www.gyan.dev/ffmpeg/builds/

The exact license obligations depend on the build that is bundled. The Gyan builds page states that its builds are GPLv3. Before selling or redistributing a package that includes those binaries, verify the exact build, license obligations, source-offer requirements, and required notices.

## Inno Setup

The installer is generated with Inno Setup.

Important references:

- Inno Setup license text: https://jrsoftware.org/files/is/license.txt
- Inno Setup commercial license page: https://jrsoftware.org/isorder.php

The Inno Setup license allows commercial use, and the project asks commercial users to purchase a license. Check the current terms before public sale.

## Python Runtime And Python Packages

The packaged application includes Python and Python packages from the local virtual environment, including FastAPI, Uvicorn, pywebview, faster-whisper, edge-tts, sounddevice, numpy, PyInstaller, and their transitive dependencies. Package metadata and license files collected by PyInstaller are included in the packaged `_internal` directory where available.

## Models And Services

Whisper model files are not bundled by default. They are downloaded into the user's local model cache. Edge TTS uses Microsoft's online speech service through the `edge-tts` package unless Windows native TTS mode is selected or used as fallback.
