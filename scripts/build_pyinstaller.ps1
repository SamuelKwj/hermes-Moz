$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$Spec = Join-Path $Root "packaging\hermes_voice.spec"
$PrepareBin = Join-Path $Root "scripts\prepare_release_bin.ps1"

Set-Location $Root
if (Test-Path $PrepareBin) {
    & $PrepareBin -Required
}

& $Python -m PyInstaller --clean --noconfirm $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$Exe = Join-Path $Root "dist\HermesVoice\HermesVoice.exe"
if (!(Test-Path $Exe)) {
    throw "PyInstaller did not produce $Exe."
}

Write-Host "PyInstaller build complete: $Exe"
