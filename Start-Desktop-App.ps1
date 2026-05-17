#requires -version 5.1
<#
.SYNOPSIS
  Starts the Kinaesthetic AI Electron desktop beta.
.DESCRIPTION
  Installs the desktop shell dependencies when needed, then launches Electron.
  The Electron app starts the local Python site server and opens player mode
  plus a topmost game overlay window.
#>
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Join-Path $ProjectDir "desktop\electron"
$PackageJson = Join-Path $DesktopDir "package.json"
$NodeModules = Join-Path $DesktopDir "node_modules"

if (-not (Test-Path $PackageJson)) {
    Write-Host "Desktop shell is missing: $PackageJson" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command "node")) {
    Write-Host "Node.js is required for the desktop beta launcher." -ForegroundColor Red
    Write-Host "Install Node.js LTS, then run this launcher again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Command "npm")) {
    Write-Host "npm is required for the desktop beta launcher." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Push-Location $DesktopDir
try {
    if (-not (Test-Path $NodeModules)) {
        Write-Host "Installing desktop shell dependencies ..." -ForegroundColor Cyan
        npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed"
        }
    }

    $env:KAI_PROJECT_ROOT = $ProjectDir
    Write-Host "Starting Kinaesthetic AI desktop beta ..." -ForegroundColor Green
    npm run start
    if ($LASTEXITCODE -ne 0) {
        throw "Electron desktop app exited with code $LASTEXITCODE"
    }
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
finally {
    Pop-Location
}
