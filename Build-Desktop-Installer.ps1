#requires -version 5.1
<#
.SYNOPSIS
  Builds the Electron installer for the Kinaesthetic AI desktop beta.
.DESCRIPTION
  This creates an NSIS installer through electron-builder. The current beta
  still expects Python/runtime files from the project layout; packaging the
  Python engine as a sidecar is the next milestone.
#>
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Join-Path $ProjectDir "desktop\electron"
$PackageJson = Join-Path $DesktopDir "package.json"

if (-not (Test-Path $PackageJson)) {
    throw "Desktop shell is missing: $PackageJson"
}

Push-Location $DesktopDir
try {
    npm install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed"
    }
    npm run dist
    if ($LASTEXITCODE -ne 0) {
        throw "electron-builder failed"
    }
    Write-Host "Installer output: $ProjectDir\dist\electron" -ForegroundColor Green
}
finally {
    Pop-Location
}
