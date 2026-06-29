[CmdletBinding()]
param(
    [switch]$Build,
    [int]$Port = 9987
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Node = Get-Command node -ErrorAction SilentlyContinue

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "== $Name =="
    & $Action
}

function Invoke-Native {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    & $Action
    $code = $LASTEXITCODE
    if ($null -ne $code -and $code -ne 0) {
        throw "$Name failed with exit code $code."
    }
}

function Wait-For-Health {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 45
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            if ($health.ok) {
                return $health
            }
        }
        catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    }

    throw "Backend health check timed out: $lastError"
}

function Wait-For-StatusReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastSummary = "status endpoint was not reached"
    while ((Get-Date) -lt $deadline) {
        try {
            $status = Invoke-RestMethod -Uri $Url -TimeoutSec 10
            $lastSummary = "stt_ready=$($status.stt_ready), tts_ready=$($status.tts_ready), last_error=$($status.last_error)"
            if ($status.stt_ready -and $status.tts_ready) {
                return $status
            }
        }
        catch {
            $lastSummary = $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }

    throw "Backend did not become ready: $lastSummary"
}

if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found: $Python"
}

if (-not $Node) {
    throw "Node.js is required for frontend JavaScript syntax checks."
}

Push-Location $Root
try {
    Invoke-Step "Python compileall" {
        Invoke-Native "Python compileall" {
            & $Python -m compileall -q launcher.py backend
        }
    }

    Invoke-Step "Frontend JavaScript syntax" {
        Invoke-Native "Frontend JavaScript syntax" {
            & $Node.Source -e "const fs=require('fs'); const html=fs.readFileSync('frontend/index.html','utf8'); const scripts=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]); scripts.forEach((s,i)=>new Function(s)); console.log('checked '+scripts.length+' script block(s)')"
        }
    }

    Invoke-Step "Dependency imports" {
        Invoke-Native "Dependency imports" {
            & $Python -c "import fastapi, uvicorn, webview, pystray, PyInstaller; import faster_whisper, edge_tts, sounddevice; import numpy, httpx; print('imports ok')"
        }
    }

    Invoke-Step "Unit tests" {
        Invoke-Native "Unit tests" {
            & $Python -m unittest discover -s tests -v
        }
    }

    Invoke-Step "Git whitespace" {
        $oldErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $whitespaceOutput = & git diff --check 2>$null
            $code = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $oldErrorActionPreference
        }
        if ($whitespaceOutput) {
            $whitespaceOutput
        }
        if ($code -ne 0) {
            throw "Git whitespace check failed with exit code $code."
        }
    }

    Invoke-Step "Backend /health and /api/status" {
        $oldPort = $env:VOICE_WIDGET_PORT
        $oldHost = $env:VOICE_WIDGET_HOST
        $env:VOICE_WIDGET_PORT = [string]$Port
        $env:VOICE_WIDGET_HOST = "127.0.0.1"
        $process = $null
        try {
            $process = Start-Process `
                -FilePath $Python `
                -ArgumentList @("backend\run_server.py") `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -PassThru

            $health = Wait-For-Health -Url "http://127.0.0.1:$Port/health"
            $status = Wait-For-StatusReady -Url "http://127.0.0.1:$Port/api/status"
            [pscustomobject]@{
                health_ok = $health.ok
                stt_ready = $status.stt_ready
                tts_ready = $status.tts_ready
                stt_model = $status.stt.model
                stt_device = $status.stt.device
                stt_compute = $status.stt.compute_type
                stt_fallback = $status.stt.fallback
            } | ConvertTo-Json -Depth 4
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

    if ($Build) {
        Invoke-Step "PyInstaller build" {
            & (Join-Path $Root "scripts\build_pyinstaller.ps1")
        }

        Invoke-Step "Inno Setup installer build" {
            & (Join-Path $Root "scripts\build_installer.ps1")
        }

        Invoke-Step "Artifact timestamps" {
            Get-Item `
                (Join-Path $Root "dist\HermesVoice\HermesVoice.exe"), `
                (Join-Path $Root "dist\installer\HermesVoiceSetup.exe") |
                Select-Object FullName, Length, LastWriteTime |
                Format-List
        }
    }
}
finally {
    Pop-Location
}
