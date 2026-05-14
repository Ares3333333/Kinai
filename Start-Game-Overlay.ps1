#requires -version 5.1
<#
.SYNOPSIS
  Starts Kinaesthetic AI site mode for recording gameplay.
.DESCRIPTION
  Opens /play for local camera CV and a small always-on-top overlay window.
  Raw video remains in the browser. The overlay reads only derived state from
  the local site server.
#>
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$SiteServerFile = Join-Path $ProjectDir "site_server.py"
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$OutLog = Join-Path $ProjectDir "site_server.log"
$ErrLog = Join-Path $ProjectDir "site_server.err.log"
$PlayUrl = "http://localhost:8502/play"
$OverlayUrl = "http://localhost:8502/overlay?mode=tiny&voice=1&window=1"
$HealthUrl = "http://localhost:8502/healthz"

Set-Location $ProjectDir

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
    param([string]$Url, [int]$Attempts = 24)
    for ($i = 0; $i -lt $Attempts; $i++) {
        if (Test-Url -Url $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Find-Edge {
    $candidates = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return "msedge.exe"
}

function Start-SiteIfNeeded {
    if (Test-Url -Url $HealthUrl) { return }
    if (-not (Test-Path $PythonExe)) {
        $systemPython = $null
        foreach ($candidate in @("python", "py", "python3")) {
            try {
                $null = & $candidate --version 2>$null
                if ($LASTEXITCODE -eq 0) { $systemPython = $candidate; break }
            } catch {}
        }
        if (-not $systemPython) { throw "Python 3.11+ is required." }
        & $systemPython -m venv $VenvDir
    }
    $markerFile = Join-Path $VenvDir ".kinaesthetic_deps_v2.marker"
    if (-not (Test-Path $markerFile)) {
        & $PythonExe -m pip install --upgrade pip --disable-pip-version-check --quiet
        & $PythonExe -m pip install -r $RequirementsFile --disable-pip-version-check --quiet
        Set-Content -Path $markerFile -Value "ok" -Encoding UTF8
    }
    Start-Process -FilePath $PythonExe -ArgumentList @($SiteServerFile) -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden | Out-Null
    $null = Wait-ForUrl -Url $HealthUrl
}

function Set-WindowTopMostByProcess {
    param([int]$ProcessId)
    Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class WinTopMost {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
  public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
  public const UInt32 SWP_NOMOVE = 0x0002;
  public const UInt32 SWP_NOSIZE = 0x0001;
  public const UInt32 SWP_SHOWWINDOW = 0x0040;
  public static void Apply(uint pid) {
    EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
      uint windowPid;
      GetWindowThreadProcessId(hWnd, out windowPid);
      if (windowPid == pid && IsWindowVisible(hWnd)) {
        StringBuilder title = new StringBuilder(256);
        GetWindowText(hWnd, title, title.Capacity);
        if (title.ToString().Contains("Kinaesthetic AI")) {
          SetWindowPos(hWnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
        }
      }
      return true;
    }, IntPtr.Zero);
  }
}
"@ -ErrorAction SilentlyContinue
    [WinTopMost]::Apply([uint32]$ProcessId)
}

Start-SiteIfNeeded

$edge = Find-Edge
Start-Process -FilePath $edge -ArgumentList @("--new-window", $PlayUrl) | Out-Null
Start-Sleep -Seconds 1
$overlayProcess = Start-Process -FilePath $edge -ArgumentList @(
    "--app=$OverlayUrl",
    "--window-size=360,260",
    "--window-position=40,40"
) -PassThru

Start-Sleep -Seconds 2
for ($i = 0; $i -lt 8; $i++) {
    try { Set-WindowTopMostByProcess -ProcessId $overlayProcess.Id } catch {}
    Start-Sleep -Milliseconds 500
}

Write-Host "Game recording mode is ready." -ForegroundColor Green
Write-Host "1. In /play: click 'Старт с камерой' and allow camera." -ForegroundColor Cyan
Write-Host "2. In the overlay: click 'Включить голос' once." -ForegroundColor Cyan
Write-Host "3. Use borderless/windowed game mode if exclusive fullscreen hides overlays." -ForegroundColor Yellow
Write-Host "Overlay: $OverlayUrl" -ForegroundColor DarkGray
Start-Sleep -Seconds 5
