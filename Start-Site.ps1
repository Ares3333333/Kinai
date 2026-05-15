#requires -version 5.1
<#
.SYNOPSIS
  Starts only the Kinaesthetic AI site server on :8502 and opens the landing page.
.DESCRIPTION
  Used by the desktop shortcut "Kinaesthetic AI - Site". This script:
    - ensures the local Python venv exists and dependencies are installed
      (so the shortcut works on a fresh machine without manual setup);
    - frees TCP port 8502 if a stale process is holding it;
    - launches site_server.py in the background;
    - waits for /api/engine-health, then opens / in the default
      browser.
  The Streamlit cockpit (port 8501) is intentionally NOT started here -
  the top desktop shortcut "Kinaesthetic AI" handles that one.
#>
$ErrorActionPreference = "Stop"

$ProjectDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir          = Join-Path $ProjectDir ".venv"
$PythonExe        = Join-Path $VenvDir "Scripts\python.exe"
$SiteServerFile   = Join-Path $ProjectDir "site_server.py"
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$OutLog           = Join-Path $ProjectDir "site_server.log"
$ErrLog           = Join-Path $ProjectDir "site_server.err.log"
$LauncherLog      = Join-Path $ProjectDir "launcher-site.log"
$SiteUrl          = "http://localhost:8502/"
$EngineHealthUrl  = "http://localhost:8502/api/engine-health"

Set-Location $ProjectDir

function Write-LauncherLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LauncherLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Stop-PortProcess {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-LauncherLog "Stopped listener PID $($listener.OwningProcess) on $Port"
        } catch {
            Write-LauncherLog "Could not stop PID $($listener.OwningProcess): $($_.Exception.Message)"
        }
    }
}

function Wait-ForUrl {
    param([string]$Url, [int]$Attempts = 18)
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 1
            if ($response.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Milliseconds 600
        }
    }
    return $false
}

function Open-Url {
    param([string]$Url)
    try {
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c start `"`" `"$Url`"" -WindowStyle Hidden
    } catch {
        Start-Process $Url
    }
}

# --- 1) Make sure Python is reachable ---------------------------------------
if (-not (Test-Path $PythonExe)) {
    $systemPython = $null
    foreach ($candidate in @("python", "py", "python3")) {
        try {
            $null = & $candidate --version 2>$null
            if ($LASTEXITCODE -eq 0) { $systemPython = $candidate; break }
        } catch {}
    }
    if (-not $systemPython) {
        Write-Host "Python 3.11+ is required. Install: https://www.python.org/downloads/" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "Creating virtualenv at $VenvDir ..." -ForegroundColor DarkGray
    & $systemPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create venv" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# --- 2) Make sure dependencies are present ----------------------------------
$markerFile = Join-Path $VenvDir ".kinaesthetic_deps_v2.marker"
if (-not (Test-Path $markerFile)) {
    Write-Host "Installing dependencies (first run)..." -ForegroundColor DarkGray
    & $PythonExe -m pip install --upgrade pip --disable-pip-version-check --quiet
    & $PythonExe -m pip install -r $RequirementsFile --disable-pip-version-check --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip install failed. Check requirements.txt." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Set-Content -Path $markerFile -Value "ok" -Encoding UTF8
}

if (-not (Test-Path $SiteServerFile)) {
    Write-Host "site_server.py was not found: $SiteServerFile" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-LauncherLog "Launcher started"

# --- 3) Free port 8502 and start site_server -------------------------------
Stop-PortProcess -Port 8502
Start-Sleep -Seconds 1

$siteProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList @($SiteServerFile) `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -WindowStyle Hidden

Write-LauncherLog "Started site_server PID $($siteProcess.Id)"
Write-Host "Kinaesthetic AI - site starting..." -ForegroundColor Green
Write-Host "Site PID: $($siteProcess.Id)" -ForegroundColor DarkGray

$ready = Wait-ForUrl -Url $EngineHealthUrl
if ($ready) {
    Write-Host "Site ready: $SiteUrl" -ForegroundColor Green
} else {
    Write-Host "Site is still starting. Open manually: $SiteUrl" -ForegroundColor Yellow
    Write-Host "If something failed, check: $ErrLog" -ForegroundColor Yellow
    Write-LauncherLog "Server not ready before timeout"
}

Open-Url -Url $SiteUrl

Write-Host ""
Write-Host "Top shortcut launches the Streamlit cockpit (port 8501)." -ForegroundColor DarkGray
Write-Host "This shortcut launches the site only (port 8502)." -ForegroundColor DarkGray
Write-Host "You can close this window. Server keeps running in background." -ForegroundColor Cyan
Start-Sleep -Seconds 3

