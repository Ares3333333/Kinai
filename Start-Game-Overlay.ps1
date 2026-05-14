#requires -version 5.1
<#
.SYNOPSIS
  Starts Kinaesthetic AI game recording mode.
.DESCRIPTION
  Opens /play for camera CV and a small always-on-top overlay window.
#>
param(
    [string]$SiteUrl = ""
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$SiteServerFile = Join-Path $ProjectDir "site_server.py"
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$OutLog = Join-Path $ProjectDir "site_server.log"
$ErrLog = Join-Path $ProjectDir "site_server.err.log"
$LauncherLog = Join-Path $ProjectDir "game_overlay_launcher.log"
$DefaultBaseUrl = if ($SiteUrl) { $SiteUrl } elseif ($env:KAI_SITE_URL) { $env:KAI_SITE_URL } else { "http://localhost:8502" }
$BaseUrl = $DefaultBaseUrl.TrimEnd("/")
$PlayUrl = "$BaseUrl/play"
$OverlayUrl = "$BaseUrl/overlay?mode=tiny&voice=1&window=1"
$HealthUrl = "$BaseUrl/healthz"
$TopMostScript = Join-Path $ProjectDir "Keep-Overlay-TopMost.ps1"

Set-Location $ProjectDir

function Write-LauncherLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LauncherLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Test-Url {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 1
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-ForUrl {
    param([string]$Url, [int]$Attempts = 90)
    for ($i = 0; $i -lt $Attempts; $i++) {
        if (Test-Url -Url $Url) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Stop-PortProcess {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-LauncherLog "Stopped PID $($listener.OwningProcess) on port $Port"
        } catch {}
    }
}

function Find-Browser {
    $candidates = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    foreach ($candidate in @("msedge.exe", "chrome.exe")) {
        try {
            $cmd = Get-Command $candidate -ErrorAction Stop
            return $cmd.Source
        } catch {}
    }
    return $null
}

function Start-SiteIfNeeded {
    if ($BaseUrl -notmatch "^http://(localhost|127\.0\.0\.1)(:\d+)?$") {
        Write-LauncherLog "Using remote site: $BaseUrl"
        return
    }

    if (-not (Test-Path $PythonExe)) {
        $systemPython = $null
        foreach ($candidate in @("python", "py", "python3")) {
            try {
                $null = & $candidate --version 2>$null
                if ($LASTEXITCODE -eq 0) { $systemPython = $candidate; break }
            } catch {}
        }
        if (-not $systemPython) { throw "Python 3.11+ is required." }
        Write-LauncherLog "Creating virtualenv"
        & $systemPython -m venv $VenvDir
    }

    $markerFile = Join-Path $VenvDir ".kinaesthetic_deps_v2.marker"
    if (-not (Test-Path $markerFile)) {
        Write-LauncherLog "Installing dependencies"
        & $PythonExe -m pip install --upgrade pip --disable-pip-version-check --quiet
        & $PythonExe -m pip install -r $RequirementsFile --disable-pip-version-check --quiet
        if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
        Set-Content -Path $markerFile -Value "ok" -Encoding UTF8
    }

    Stop-PortProcess -Port 8502
    Start-Sleep -Seconds 1
    Write-LauncherLog "Starting clean site_server"
    Start-Process -FilePath $PythonExe -ArgumentList @($SiteServerFile) -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden | Out-Null

    Start-Sleep -Seconds 7
    if (Test-Url -Url $HealthUrl) { Write-LauncherLog "Health OK" } else { Write-LauncherLog "Health not ready yet; opening browser anyway" }
}

try {
    Write-LauncherLog "Launcher started"
    Start-SiteIfNeeded

    $browser = Find-Browser
    if (-not $browser) { throw "Microsoft Edge or Google Chrome was not found." }
    Write-LauncherLog "Browser: $browser"

    Start-Process -FilePath $browser -ArgumentList @("--new-window", $PlayUrl) | Out-Null
    Start-Sleep -Seconds 1

    $overlayProcess = Start-Process -FilePath $browser -ArgumentList @(
        "--app=$OverlayUrl",
        "--window-size=360,260",
        "--window-position=40,40"
    ) -PassThru
    Write-LauncherLog "Overlay process PID: $($overlayProcess.Id)"

    if (Test-Path $TopMostScript) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $TopMostScript,
            "-Title", "Kinaesthetic AI - game overlay",
            "-Seconds", "7200",
            "-X", "40",
            "-Y", "40",
            "-Width", "360",
            "-Height", "260"
        ) -WindowStyle Hidden | Out-Null
        Write-LauncherLog "TopMost watchdog started"
    }

    Write-Host "Game recording mode is ready." -ForegroundColor Green
    Write-Host "1. In /play: click Start with camera and allow camera." -ForegroundColor Cyan
    Write-Host "2. In the overlay: click Enable voice once." -ForegroundColor Cyan
    Write-Host "3. Use borderless/windowed game mode if exclusive fullscreen hides overlays." -ForegroundColor Yellow
    Write-Host "Overlay: $OverlayUrl" -ForegroundColor DarkGray
    Write-LauncherLog "Launcher completed"
    Start-Sleep -Seconds 5
} catch {
    Write-LauncherLog "ERROR: $($_.Exception.Message)"
    Write-Host "Could not start game recording mode:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Log: $LauncherLog" -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}
