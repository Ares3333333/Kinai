#requires -version 5.1
<#
.SYNOPSIS
  Starts only the Kinaesthetic AI Streamlit cockpit on :8501.
.DESCRIPTION
  Used by the desktop shortcut "Kinaesthetic AI" (the top one). This
  script:
    - ensures the local Python venv exists and dependencies are
      installed (so the shortcut works on a fresh machine);
    - frees TCP port 8501 if a stale Streamlit is still bound;
    - launches app.py via Streamlit in the background;
    - waits for http://localhost:8501 and opens the browser.
  The site / play page (port 8502) is intentionally NOT started here -
  the bottom desktop shortcut "Kinaesthetic AI - Site" handles that.
#>
$ErrorActionPreference = "Stop"

$ProjectDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir          = Join-Path $ProjectDir ".venv"
$PythonExe        = Join-Path $VenvDir "Scripts\python.exe"
$AppFile          = Join-Path $ProjectDir "app.py"
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$OutLog           = Join-Path $ProjectDir "streamlit.log"
$ErrLog           = Join-Path $ProjectDir "streamlit.err.log"
$LauncherLog      = Join-Path $ProjectDir "launcher-app.log"
$Url              = "http://localhost:8501"

Set-Location $ProjectDir

function Write-LauncherLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LauncherLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Stop-ExistingApp {
    $listeners = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-LauncherLog "Stopped listener PID $($listener.OwningProcess) on 8501"
        } catch {
            Write-LauncherLog "Could not stop listener: $($_.Exception.Message)"
        }
    }
    $streamlitProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match "python" -and (
                $_.CommandLine -like "*streamlit*run*$AppFile*" -or
                $_.CommandLine -like "*streamlit*run*app.py*"
            )
        }
    foreach ($processInfo in $streamlitProcesses) {
        try {
            Stop-Process -Id $processInfo.ProcessId -Force -ErrorAction SilentlyContinue
            Write-LauncherLog "Stopped stale Streamlit PID $($processInfo.ProcessId)"
        } catch {
            Write-LauncherLog "Could not stop stale Streamlit: $($_.Exception.Message)"
        }
    }
}

function Wait-ForUrl {
    param([string]$TargetUrl, [int]$Attempts = 18)
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $TargetUrl -TimeoutSec 1
            if ($response.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Milliseconds 600
        }
    }
    return $false
}

function Open-AppUrl {
    param([string]$TargetUrl)
    try {
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c start `"`" `"$TargetUrl`"" -WindowStyle Hidden
    } catch {
        Start-Process $TargetUrl
    }
}

# --- 1) Make sure Python venv is in place -----------------------------------
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

if (-not (Test-Path $AppFile)) {
    Write-Host "app.py was not found: $AppFile" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-LauncherLog "Launcher started"
Stop-ExistingApp
Start-Sleep -Seconds 1

# --- 3) Launch Streamlit cockpit -------------------------------------------
$streamlitArgs = @(
    "-m", "streamlit", "run", $AppFile,
    "--server.port", "8501",
    "--server.address", "localhost",
    "--server.headless", "true"
)

$process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $streamlitArgs `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -WindowStyle Hidden

Write-LauncherLog "Started Streamlit PID $($process.Id)"
Write-Host "Kinaesthetic AI - cockpit starting..." -ForegroundColor Green
Write-Host "Streamlit PID: $($process.Id)" -ForegroundColor DarkGray

$ready = Wait-ForUrl -TargetUrl $Url
if ($ready) {
    Write-Host "Cockpit ready: $Url" -ForegroundColor Green
} else {
    Write-Host "Cockpit is still starting. Open manually: $Url" -ForegroundColor Yellow
    Write-Host "If something failed, check: $ErrLog" -ForegroundColor Yellow
    Write-LauncherLog "Server not ready before timeout"
}

Open-AppUrl -TargetUrl $Url

Write-Host ""
Write-Host "Top shortcut launches the Streamlit cockpit (port 8501)." -ForegroundColor DarkGray
Write-Host "Bottom shortcut launches the site only (port 8502)." -ForegroundColor DarkGray
Write-Host "You can close this window. Cockpit keeps running in background." -ForegroundColor Cyan
Start-Sleep -Seconds 3
