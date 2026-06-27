$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Uv = Get-Command uv -ErrorAction SilentlyContinue

if (!(Test-Path $Python)) {
    python -m venv $Venv
}

if ($Uv) {
    & uv pip install --python $Python --upgrade pip
    & uv pip install --python $Python -r (Join-Path $Root "backend\requirements.txt")
}
else {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $Root "backend\requirements.txt")
}
& $Python -c "import fastapi, uvicorn, webview, pystray, PyInstaller; import faster_whisper, edge_tts, sounddevice"

Write-Host "Setup complete. Start with: .\scripts\start.ps1"
