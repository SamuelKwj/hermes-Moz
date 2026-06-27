$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (!(Test-Path $Python)) {
    throw "Virtual environment not found. Run .\scripts\setup.ps1 first."
}

Set-Location $Root
& $Python launcher.py
