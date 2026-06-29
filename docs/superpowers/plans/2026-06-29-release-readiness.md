# Hermes Voice Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current Hermes Voice branch suitable for a verified internal release package and identify any remaining blockers before public sale.

**Architecture:** Keep runtime behavior local and conservative: add tests around fragile release paths, improve TTS fallback without changing the primary Edge TTS path, add a repeatable release smoke script, then rebuild and smoke-test packaged artifacts. Do not bundle large third-party binaries into git; prepare local packaging inputs only when building release artifacts.

**Tech Stack:** Python 3.11, FastAPI, pywebview, faster-whisper, edge-tts, PowerShell release scripts, PyInstaller, Inno Setup.

---

### Task 1: Baseline Release Checks

**Files:**
- Read: `README.md`
- Read: `docs/productization_status.md`
- Read: `backend/requirements.txt`
- Read: `packaging/hermes_voice.spec`
- Read: `installer/inno/HermesVoice.iss`

- [ ] **Step 1: Capture git state**

Run:

```powershell
git status --short --branch
git log -1 --oneline --decorate
```

Expected: current branch and clean/dirty state are known before edits.

- [ ] **Step 2: Run static baseline**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q launcher.py backend
node -e "const fs=require('fs'); const html=fs.readFileSync('frontend/index.html','utf8'); const scripts=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]); scripts.forEach((s,i)=>new Function(s)); console.log('checked '+scripts.length+' script block(s)')"
.\.venv\Scripts\python.exe -c "import fastapi, uvicorn, webview, pystray, PyInstaller; import faster_whisper, edge_tts, sounddevice; import numpy, httpx; print('imports ok')"
git diff --check
```

Expected: all commands exit 0.

### Task 2: Add Release Smoke Test Script

**Files:**
- Create: `tests/test_release_contracts.py`
- Create: `scripts/release_smoke.ps1`

- [ ] **Step 1: Write failing tests for release contracts**

Create tests that verify:

```python
def test_installer_reads_pyinstaller_dist():
    assert "dist\\HermesVoice\\*" in installer_text

def test_pyinstaller_bundles_frontend_assets_and_optional_bin():
    assert "(str(ROOT / \"frontend\"), \"frontend\")" in spec_text
    assert "(str(ROOT / \"assets\"), \"assets\")" in spec_text
    assert "ROOT / \"bin\"" in spec_text

def test_release_smoke_script_exists_and_checks_required_gates():
    assert script.exists()
    assert "compileall" in script_text
    assert "/health" in script_text
    assert "build_pyinstaller.ps1" in script_text
```

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_release_contracts -v
```

Expected: fails because the smoke script does not exist yet.

- [ ] **Step 2: Implement `scripts/release_smoke.ps1`**

The script must:

```powershell
param(
  [switch]$Build,
  [int]$Port = 9987
)
```

Then run compile, frontend JavaScript parse, dependency imports, whitespace check, temporary backend `/health` and `/api/status` checks, and optionally `scripts/build_pyinstaller.ps1` plus `scripts/build_installer.ps1`.

- [ ] **Step 3: Re-run release contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_release_contracts -v
```

Expected: pass.

### Task 3: Harden TTS Failure Path

**Files:**
- Create or modify: `tests/test_tts_engine.py`
- Modify: `backend/tts_engine.py`

- [ ] **Step 1: Write failing TTS fallback test**

Create a unittest that patches `edge_tts.Communicate.save` to fail twice and patches `_synthesize_windows_native` to return a wav path:

```python
def test_edge_tts_falls_back_to_windows_native_after_two_failures(self):
    async def run():
        path = await tts_engine._synthesize_edge_mp3("我在，大王请说。", settings)
        self.assertEqual(path, "fallback.wav")
    asyncio.run(run())
```

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tts_engine -v
```

Expected: fails because `_synthesize_edge_mp3` raises after the second Edge TTS failure.

- [ ] **Step 2: Implement minimal fallback**

After the second Edge TTS failure, log the error and call:

```python
return await asyncio.to_thread(_synthesize_windows_native, text, tts_settings)
```

- [ ] **Step 3: Re-run TTS tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tts_engine -v
```

Expected: pass.

### Task 4: Rebuild Current Artifacts

**Files:**
- Output only: `dist/HermesVoice`
- Output only: `dist/installer/HermesVoiceSetup.exe`

- [ ] **Step 1: Run release smoke without build**

Run:

```powershell
.\scripts\release_smoke.ps1
```

Expected: static checks, dependency imports, and temporary backend smoke pass.

- [ ] **Step 2: Rebuild package and installer**

Run:

```powershell
.\scripts\release_smoke.ps1 -Build
```

Expected: PyInstaller and Inno Setup produce updated artifacts.

- [ ] **Step 3: Smoke packaged executable**

Run the packaged exe on a temporary port and verify:

```powershell
Invoke-RestMethod http://127.0.0.1:<temp-port>/health
```

Expected: `ok=true`.

### Task 5: Update Release Notes And Git

**Files:**
- Modify: `docs/productization_status.md`

- [ ] **Step 1: Update productization status**

Record the fresh commands that passed, artifact timestamps, and any remaining unverified items.

- [ ] **Step 2: Final verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\release_smoke.ps1
git status --short --branch
```

Expected: tests and smoke pass; only intended docs/script/code changes are present.

- [ ] **Step 3: Commit and push**

Run:

```powershell
git add backend/tts_engine.py tests scripts docs
git commit -m "Prepare verified release smoke checks"
git push origin HEAD
```

Expected: current branch is pushed.
