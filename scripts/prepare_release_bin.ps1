[CmdletBinding()]
param(
    [switch]$Required
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Bin = Join-Path $Root "bin"
$Tools = @("ffmpeg", "ffplay")

New-Item -ItemType Directory -Force -Path $Bin | Out-Null

foreach ($tool in $Tools) {
    $command = Get-Command "$tool.exe" -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command $tool -ErrorAction SilentlyContinue
    }

    if (-not $command -or -not $command.Source) {
        $message = "$tool not found in PATH. Install ffmpeg or add it to PATH before building release artifacts."
        if ($Required) {
            throw $message
        }
        Write-Warning $message
        continue
    }

    $target = Join-Path $Bin "$tool.exe"
    Copy-Item -LiteralPath $command.Source -Destination $target -Force
    Write-Host "Prepared bundled $tool`: $target"
}
