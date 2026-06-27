$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Spec = Join-Path $Root "installer\inno\HermesVoice.iss"
$IsccCandidates = @(
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)

$Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (!$Iscc) {
    throw "Inno Setup 6 not found. Install it, then rerun scripts\build_installer.ps1."
}

$AppExe = Join-Path $Root "dist\HermesVoice\HermesVoice.exe"
if (!(Test-Path $AppExe)) {
    throw "PyInstaller output not found. Run scripts\build_pyinstaller.ps1 first."
}

Set-Location $Root
& $Iscc $Spec
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

$Installer = Join-Path $Root "dist\installer\HermesVoiceSetup.exe"
if (!(Test-Path $Installer)) {
    throw "Inno Setup did not produce $Installer."
}

Write-Host "Installer build complete: $Installer"
