[CmdletBinding()]
param(
    [switch]$SkipSmoke,
    [switch]$RequireSigned,
    [switch]$RequireExternalEvidence,
    [string]$EvidencePath
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$PortableExe = Join-Path $Root "dist\HermesVoice\HermesVoice.exe"
$InstallerExe = Join-Path $Root "dist\installer\HermesVoiceSetup.exe"
$ReleaseSmoke = Join-Path $Root "scripts\release_smoke.ps1"
$PackageSmoke = Join-Path $Root "scripts\package_smoke.ps1"

function Add-GateResult {
    param(
        [System.Collections.Generic.List[object]]$Results,
        [string]$Name,
        [bool]$Passed,
        [string]$Message
    )

    $Results.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        message = $Message
    })
}

function Test-Signature {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return [pscustomobject]@{
            exists = $false
            signed = $false
            status = "Missing"
            subject = $null
        }
    }

    $signature = Get-AuthenticodeSignature -FilePath $Path
    return [pscustomobject]@{
        exists = $true
        signed = $signature.Status -eq "Valid"
        status = [string]$signature.Status
        subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
    }
}

function Read-Evidence {
    param([string]$Path)

    if (-not $Path) {
        return $null
    }
    if (-not (Test-Path $Path)) {
        throw "Evidence file not found: $Path"
    }
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Test-EvidenceFlag {
    param(
        $Evidence,
        [string]$Name
    )

    if (-not $Evidence) {
        return $false
    }
    $value = $Evidence.PSObject.Properties[$Name].Value
    return $value -eq $true
}

$results = [System.Collections.Generic.List[object]]::new()

if (-not $SkipSmoke) {
    try {
        & $ReleaseSmoke
        Add-GateResult $results "release_smoke" $true "Source/runtime release smoke passed."
    }
    catch {
        Add-GateResult $results "release_smoke" $false $_.Exception.Message
    }

    try {
        & $PackageSmoke -Installer -UninstallIfIsolated
        Add-GateResult $results "package_smoke" $true "Portable and installer package smoke passed."
    }
    catch {
        Add-GateResult $results "package_smoke" $false $_.Exception.Message
    }
}
else {
    Add-GateResult $results "release_smoke" $true "Skipped by request."
    Add-GateResult $results "package_smoke" $true "Skipped by request."
}

$portableSignature = Test-Signature -Path $PortableExe
$installerSignature = Test-Signature -Path $InstallerExe
$signaturesPassed = $portableSignature.signed -and $installerSignature.signed
$signatureMessage = "portable=$($portableSignature.status); installer=$($installerSignature.status)"
Add-GateResult $results "code_signing" $signaturesPassed $signatureMessage

$evidence = Read-Evidence -Path $EvidencePath
$externalGateNames = @(
    "clean_windows",
    "webview2",
    "microphone",
    "license_compliance",
    "model_terms",
    "privacy_review"
)
foreach ($gateName in $externalGateNames) {
    $passed = Test-EvidenceFlag -Evidence $evidence -Name $gateName
    Add-GateResult $results $gateName $passed ($(if ($passed) { "Evidence marked true." } else { "Evidence missing or false." }))
}

$internalReady = ($results | Where-Object { $_.name -in @("release_smoke", "package_smoke") -and -not $_.passed }).Count -eq 0
$publicReady = ($results | Where-Object { -not $_.passed }).Count -eq 0

$summary = [pscustomobject]@{
    internal_release_ready = $internalReady
    public_sale_ready = $publicReady
    require_signed = [bool]$RequireSigned
    require_external_evidence = [bool]$RequireExternalEvidence
    portable_signature = $portableSignature
    installer_signature = $installerSignature
    results = $results
}

$summary | ConvertTo-Json -Depth 6

if (-not $publicReady -and ($RequireSigned -or $RequireExternalEvidence)) {
    exit 1
}
