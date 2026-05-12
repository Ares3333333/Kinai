$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$AppFile = Join-Path $ProjectDir "app.py"
$SiteServerFile = Join-Path $ProjectDir "site_server.py"
$StreamlitOutLog = Join-Path $ProjectDir "streamlit.log"
$StreamlitErrLog = Join-Path $ProjectDir "streamlit.err.log"
$SiteOutLog = Join-Path $ProjectDir "site_server.log"
$SiteErrLog = Join-Path $ProjectDir "site_server.err.log"
$MvpUrl = "http://localhost:8501"
$PitchUrl = "http://localhost:8502"
$DemoUrl = "http://localhost:8502/demo"
$OverlayUrl = "http://localhost:8502/overlay"
$ReportUrl = "http://localhost:8502/report"
$EngineHealthUrl = "http://localhost:8502/api/engine-health"
$DataQualityUrl = "http://localhost:8502/api/data-quality"

Set-Location $ProjectDir

function Stop-PortProcess {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Host "Could not stop process on port ${Port}: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

function Wait-ForUrl {
    param(
        [string]$Url,
        [int]$Attempts = 6
    )
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
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

if (-not (Test-Path $PythonExe)) {
    Write-Host "Python virtual environment was not found: $PythonExe" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path $AppFile)) {
    Write-Host "app.py was not found: $AppFile" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path $SiteServerFile)) {
    Write-Host "site_server.py was not found: $SiteServerFile" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Starting Kinaesthetic AI full stack..." -ForegroundColor Green
Write-Host "Project: $ProjectDir" -ForegroundColor DarkGray

Stop-PortProcess -Port 8501
Stop-PortProcess -Port 8502
Start-Sleep -Seconds 1

$streamlitArgs = @(
    "-m", "streamlit", "run", $AppFile,
    "--server.port", "8501",
    "--server.address", "localhost",
    "--server.headless", "true"
)

$streamlitProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $streamlitArgs `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $StreamlitOutLog `
    -RedirectStandardError $StreamlitErrLog `
    -PassThru `
    -WindowStyle Hidden

$siteProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList @($SiteServerFile) `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $SiteOutLog `
    -RedirectStandardError $SiteErrLog `
    -PassThru `
    -WindowStyle Hidden

Write-Host "Streamlit PID: $($streamlitProcess.Id)" -ForegroundColor DarkGray
Write-Host "Pitch/overlay PID: $($siteProcess.Id)" -ForegroundColor DarkGray
Write-Host "Opening browser..." -ForegroundColor DarkGray

Start-Sleep -Seconds 4
Open-Url -Url $PitchUrl

Write-Host "Checking servers..." -ForegroundColor DarkGray

$mvpReady = Wait-ForUrl -Url $MvpUrl
$pitchReady = Wait-ForUrl -Url $PitchUrl
$engineHealthReady = Wait-ForUrl -Url $EngineHealthUrl

if ($mvpReady) {
    Write-Host "MVP cockpit ready: $MvpUrl" -ForegroundColor Green
} else {
    Write-Host "MVP cockpit is still starting. Check: $StreamlitErrLog" -ForegroundColor Yellow
}

if ($pitchReady) {
    Write-Host "Landing site ready: $PitchUrl" -ForegroundColor Green
    Write-Host "Product demo ready: $DemoUrl" -ForegroundColor Green
    Write-Host "Overlay ready:      $OverlayUrl" -ForegroundColor Green
} else {
    Write-Host "Pitch/overlay server is still starting. Check: $SiteErrLog" -ForegroundColor Yellow
}

if ($engineHealthReady) {
    Write-Host "Engine health API ready: $EngineHealthUrl" -ForegroundColor Green
} else {
    Write-Host "Engine health API is not ready yet." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "URLs:" -ForegroundColor Cyan
Write-Host "  Landing site:     $PitchUrl"
Write-Host "  Product demo:     $DemoUrl"
Write-Host "  MVP cockpit:      $MvpUrl"
Write-Host "  Pitch site:       $PitchUrl"
Write-Host "  Session report:   $ReportUrl"
Write-Host "  Overlay streamer: $OverlayUrl?mode=streamer"
Write-Host "  Overlay coach:    $OverlayUrl?mode=coach"
Write-Host "  Overlay minimal:  $OverlayUrl?mode=minimal"
Write-Host "  API state:        http://localhost:8502/api/state"
Write-Host "  Engine health:    $EngineHealthUrl"
Write-Host "  Data quality:     $DataQualityUrl"
Write-Host ""
Write-Host "You can close this window. Both servers will keep running in the background." -ForegroundColor Cyan
Start-Sleep -Seconds 6
