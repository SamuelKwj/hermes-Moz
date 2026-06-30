[CmdletBinding()]
param(
    [int]$PortablePort = 9991,
    [int]$InstallerPort = 9992,
    [switch]$Installer,
    [switch]$UninstallIfIsolated
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$PortableExe = Join-Path $Root "dist\HermesVoice\HermesVoice.exe"
$InstallerExe = Join-Path $Root "dist\installer\HermesVoiceSetup.exe"
$WorkRoot = Join-Path $Root "work"

function Wait-For-StatusReady {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastSummary = "status endpoint was not reached"
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 10
            $lastSummary = "health_ok=$($health.ok), stt_ready=$($status.stt_ready), tts_ready=$($status.tts_ready), last_error=$($status.last_error)"
            if ($health.ok -and $status.stt_ready -and $status.tts_ready) {
                return [pscustomobject]@{
                    health = $health
                    status = $status
                }
            }
        }
        catch {
            $lastSummary = $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }

    throw "Packaged app did not become ready: $lastSummary"
}

function Invoke-AppSmoke {
    param(
        [string]$Exe,
        [int]$Port,
        [string]$Label
    )

    if (-not (Test-Path $Exe)) {
        throw "$Label executable not found: $Exe"
    }
    $exeDir = Split-Path -Parent $Exe
    $noticeCandidates = @(
        (Join-Path $exeDir "THIRD_PARTY_NOTICES.md"),
        (Join-Path $exeDir "_internal\THIRD_PARTY_NOTICES.md")
    )
    $noticePath = $noticeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $noticePath) {
        throw "$Label package is missing THIRD_PARTY_NOTICES.md."
    }

    $oldPort = $env:VOICE_WIDGET_PORT
    $oldHost = $env:VOICE_WIDGET_HOST
    $env:VOICE_WIDGET_PORT = [string]$Port
    $env:VOICE_WIDGET_HOST = "127.0.0.1"
    $process = $null
    try {
        $process = Start-Process `
            -FilePath $Exe `
            -WorkingDirectory (Split-Path -Parent $Exe) `
            -WindowStyle Hidden `
            -PassThru

        $ready = Wait-For-StatusReady -Port $Port
        $status = $ready.status
        if (-not $status.dependencies.ffmpeg -or -not $status.dependencies.ffplay) {
            throw "$Label did not detect bundled ffmpeg/ffplay."
        }

        [pscustomobject]@{
            label = $Label
            exe = $Exe
            health_ok = $ready.health.ok
            stt_ready = $status.stt_ready
            tts_ready = $status.tts_ready
            stt_model = $status.stt.model
            stt_device = $status.stt.device
            stt_compute = $status.stt.compute_type
            ffmpeg = $status.dependencies.ffmpeg
            ffplay = $status.dependencies.ffplay
        }
    }
    finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit(5000) | Out-Null
        }
        $env:VOICE_WIDGET_PORT = $oldPort
        $env:VOICE_WIDGET_HOST = $oldHost
    }
}

function Get-HermesInstallDir {
    $appIdKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{FBC0C2F1-D8D8-4D70-8F07-4E23D2B31759}_is1"
    $appIdEntry = Get-ItemProperty $appIdKey -ErrorAction SilentlyContinue
    if ($appIdEntry) {
        $installLocation = [string]$appIdEntry.InstallLocation
        $uninstall = [string]$appIdEntry.UninstallString
        if ($installLocation -and (Test-Path $installLocation)) {
            return [pscustomobject]@{
                InstallDir = $installLocation.TrimEnd("\")
                UninstallString = $uninstall
            }
        }
        if ($uninstall -match '"([^"]+\\)unins\d+\.exe"') {
            return [pscustomobject]@{
                InstallDir = $Matches[1].TrimEnd("\")
                UninstallString = $uninstall
            }
        }
    }

    $roots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )

    foreach ($rootKey in $roots) {
        $entry = Get-ItemProperty $rootKey -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like "Hermes Voice*" -or $_.DisplayName -like "HermesVoice*" } |
            Select-Object -First 1
        if ($entry) {
            $uninstall = [string]$entry.UninstallString
            $installLocation = [string]$entry.InstallLocation
            if ($installLocation -and (Test-Path $installLocation)) {
                return [pscustomobject]@{
                    InstallDir = $installLocation
                    UninstallString = $uninstall
                }
            }
            if ($uninstall -match '"([^"]+\\)unins\d+\.exe"') {
                return [pscustomobject]@{
                    InstallDir = $Matches[1].TrimEnd("\")
                    UninstallString = $uninstall
                }
            }
        }
    }

    $fallback = Join-Path $env:LOCALAPPDATA "Programs\Hermes Voice"
    if (Test-Path (Join-Path $fallback "HermesVoice.exe")) {
        return [pscustomobject]@{
            InstallDir = $fallback
            UninstallString = Join-Path $fallback "unins000.exe"
        }
    }

    return $null
}

function Invoke-InstallerSmoke {
    if (-not (Test-Path $InstallerExe)) {
        throw "Installer not found: $InstallerExe"
    }

    New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
    $installDir = Join-Path $WorkRoot ("install-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    $installLog = Join-Path $WorkRoot ("installer-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
    $sourceExeTimestamp = (Get-Item $PortableExe).LastWriteTime
    $installArgs = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/TASKS=", "/DIR=$installDir", "/LOG=$installLog")
    $installerProcess = Start-Process `
        -FilePath $InstallerExe `
        -ArgumentList $installArgs `
        -WindowStyle Hidden `
        -Wait `
        -PassThru

    if ($installerProcess.ExitCode -ne 0) {
        throw "Installer failed with exit code $($installerProcess.ExitCode)."
    }

    $installed = Get-HermesInstallDir
    if (-not $installed) {
        throw "Could not find installed Hermes Voice entry after installer smoke."
    }

    $installedExe = Join-Path $installed.InstallDir "HermesVoice.exe"
    if (-not (Test-Path $installedExe)) {
        throw "Installed exe not found after installer smoke: $installedExe. Log: $installLog"
    }
    $installedTimestamp = (Get-Item $installedExe).LastWriteTime
    if ($installedTimestamp -lt $sourceExeTimestamp.AddMinutes(-2)) {
        throw "Installer did not update installed executable. Installed=$installedTimestamp, sourceExeTimestamp=$sourceExeTimestamp. Log: $installLog"
    }
    $result = Invoke-AppSmoke -Exe $installedExe -Port $InstallerPort -Label "installer"
    $result | Add-Member -NotePropertyName install_dir -NotePropertyValue $installed.InstallDir
    $result | Add-Member -NotePropertyName requested_install_dir -NotePropertyValue $installDir
    $result | Add-Member -NotePropertyName used_existing_install_record -NotePropertyValue ($installed.InstallDir -ne $installDir)

    if ($UninstallIfIsolated -and $installed.InstallDir -eq $installDir) {
        $resolvedWork = (Resolve-Path $WorkRoot).Path
        $resolvedInstall = (Resolve-Path $installed.InstallDir).Path
        if (-not $resolvedInstall.StartsWith($resolvedWork, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to uninstall outside smoke work root: $resolvedInstall"
        }
        $uninstaller = Join-Path $installed.InstallDir "unins000.exe"
        if (-not (Test-Path $uninstaller)) {
            throw "Uninstaller not found: $uninstaller"
        }
        $uninstallProcess = Start-Process `
            -FilePath $uninstaller `
            -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($uninstallProcess.ExitCode -ne 0) {
            throw "Uninstaller failed with exit code $($uninstallProcess.ExitCode)."
        }
        $result | Add-Member -NotePropertyName uninstalled_isolated_install -NotePropertyValue $true
    }

    return $result
}

$results = @()
$results += Invoke-AppSmoke -Exe $PortableExe -Port $PortablePort -Label "portable"
if ($Installer) {
    $results += Invoke-InstallerSmoke
}

$results | ConvertTo-Json -Depth 5
