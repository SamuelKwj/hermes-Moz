[CmdletBinding()]
param(
    [string]$PfxPath,
    [string]$PfxPassword,
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$MachineStore,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Targets = @(
    (Join-Path $Root "dist\HermesVoice\HermesVoice.exe"),
    (Join-Path $Root "dist\installer\HermesVoiceSetup.exe")
)

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $kitRoots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    )
    foreach ($kitRoot in $kitRoots) {
        if (-not (Test-Path $kitRoot)) {
            continue
        }
        $candidate = Get-ChildItem -LiteralPath $kitRoot -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like "*\x64\signtool.exe" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }

    throw "signtool.exe not found. Install Windows SDK or add signtool.exe to PATH."
}

function Assert-Targets {
    foreach ($target in $Targets) {
        if (-not (Test-Path $target)) {
            throw "Release artifact not found: $target"
        }
    }
}

function Invoke-SignTool {
    param(
        [string]$SignTool,
        [string[]]$Arguments
    )

    & $SignTool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "signtool failed with exit code $LASTEXITCODE."
    }
}

function Assert-ValidSignature {
    param([string]$Path)

    $signature = Get-AuthenticodeSignature -FilePath $Path
    if ($signature.Status -ne "Valid") {
        throw "Invalid Authenticode signature for $Path`: $($signature.Status)"
    }
    return [pscustomobject]@{
        path = $Path
        status = [string]$signature.Status
        subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
    }
}

Assert-Targets

if (-not $VerifyOnly) {
    if (-not $PfxPath -and -not $CertificateThumbprint) {
        throw "Provide either -PfxPath or -CertificateThumbprint, or use -VerifyOnly."
    }

    $signTool = Find-SignTool
    foreach ($target in $Targets) {
        if ($PfxPath) {
            if (-not (Test-Path $PfxPath)) {
                throw "PFX file not found: $PfxPath"
            }
            $args = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/f", $PfxPath)
            if ($PfxPassword) {
                $args += @("/p", $PfxPassword)
            }
            $args += $target
            Invoke-SignTool -SignTool $signTool -Arguments $args
        }
        else {
            $args = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/sha1", $CertificateThumbprint)
            if ($MachineStore) {
                $args += "/sm"
            }
            $args += $target
            Invoke-SignTool -SignTool $signTool -Arguments $args
        }
    }
}

$Targets | ForEach-Object { Assert-ValidSignature -Path $_ } | ConvertTo-Json -Depth 4
