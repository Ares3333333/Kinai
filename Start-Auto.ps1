#requires -version 5.1
<#
.SYNOPSIS
  One-click bootstrap for Kinaesthetic AI testers.
.DESCRIPTION
  Creates a local virtualenv if missing, installs dependencies, then
  starts both servers (Streamlit cockpit on :8501 and pitch/play site
  on :8502). Finally opens the browser at /play.

  Designed so a non-technical gamer friend can run a single command
  with nothing pre-installed except Python 3.11+.
#>
$ErrorActionPreference = "Stop"

$ProjectDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir          = Join-Path $ProjectDir ".venv"
$PythonExe        = Join-Path $VenvDir "Scripts\python.exe"
$AppFile          = Join-Path $ProjectDir "app.py"
$SiteServerFile   = Join-Path $ProjectDir "site_server.py"
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$StreamlitOutLog  = Join-Path $ProjectDir "streamlit.log"
$StreamlitErrLog  = Join-Path $ProjectDir "streamlit.err.log"
$SiteOutLog       = Join-Path $ProjectDir "site_server.log"
$SiteErrLog       = Join-Path $ProjectDir "site_server.err.log"
$PlayUrl          = "http://localhost:8502/play"
$EngineHealthUrl  = "http://localhost:8502/api/engine-health"

Set-Location $ProjectDir

function Stop-PortProcess {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Host "Could not stop process on port $Port" -ForegroundColor Yellow
        }
    }
}

function Wait-ForUrl {
    param([string]$Url, [int]$Attempts = 12)
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
        Start-Process $Url
    } catch {
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c start `"`" `"$Url`"" -WindowStyle Hidden
    }
}

# 1) Locate Python
$systemPython = $null
foreach ($candidate in @("python", "py", "python3")) {
    try {
        $version = & $candidate --version 2>$null
        if ($LASTEXITCODE -eq 0) { $systemPython = $candidate; break }
    } catch {}
}

if (-not $systemPython) {
    Write-Host "Python 3.11+ is required. Install from https://www.python.org/downloads/" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# 2) Create virtualenv if missing
if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating virtualenv at $VenvDir ..." -ForegroundColor DarkGray
    & $systemPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create venv" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# 3) Install dependencies (fast no-op if already up to date)
Write-Host "Ensuring dependencies are installed ..." -ForegroundColor DarkGray
& $PythonExe -m pip install --upgrade pip --disable-pip-version-check --quiet
& $PythonExe -m pip install -r $RequirementsFile --disable-pip-version-check --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Check requirements.txt and your internet." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# 4) Stop anything lingering on our ports
Stop-PortProcess -Port 8501
Stop-PortProcess -Port 8502
Start-Sleep -Seconds 1

# 5) Launch Streamlit and the pitch/site server
$streamlitArgs = @(
    "-m", "streamlit", "run", $AppFile,
    "--server.port", "8501",
    "--server.address", "localhost",
    "--server.headless", "true"
)

Start-Process -FilePath $PythonExe -ArgumentList $streamlitArgs -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $StreamlitOutLog -RedirectStandardError $StreamlitErrLog `
    -WindowStyle Hidden | Out-Null

Start-Process -FilePath $PythonExe -ArgumentList @($SiteServerFile) -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $SiteOutLog -RedirectStandardError $SiteErrLog `
    -WindowStyle Hidden | Out-Null

Write-Host "Booting Kinaesthetic AI ..." -ForegroundColor Green
$ready = Wait-ForUrl -Url $EngineHealthUrl -Attempts 24
if ($ready) {
    Write-Host "Backend ready. Opening $PlayUrl" -ForegroundColor Green
} else {
    Write-Host "Backend slow to start. Logs: $SiteErrLog" -ForegroundColor Yellow
}

Open-Url -Url $PlayUrl

Write-Host ""
Write-Host "URLs:" -ForegroundColor Cyan
Write-Host "  /play  (single-screen tester)  $PlayUrl"
Write-Host "  /demo  (investor cockpit)       http://localhost:8502/demo"
Write-Host "  /metrics                        http://localhost:8502/metrics"
Write-Host "  /pitch                          http://localhost:8502/pitch"
Write-Host "  Streamlit cockpit              http://localhost:8501"
Write-Host ""
Write-Host "You can close this window. Servers keep running in the background." -ForegroundColor DarkGray
Start-Sleep -Seconds 3
