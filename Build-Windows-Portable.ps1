#requires -version 5.1
<#
.SYNOPSIS
  Builds a portable Windows folder/zip for the local Kinaesthetic AI app.
.DESCRIPTION
  This is the first downloadable-PC-app path: no cloud deploy, no raw media
  upload, no installer magic. The user unzips the folder and launches one of
  the Start-*.bat files. First run creates .venv and installs
  requirements-local.txt on the user's machine.
#>
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistDir = Join-Path $ProjectDir "dist"
$AppDir = Join-Path $DistDir "KinaestheticAI-Desktop"
$ZipPath = Join-Path $DistDir "KinaestheticAI-Desktop.zip"

Set-Location $ProjectDir

function Assert-InProject {
    param([string]$Path)
    $resolvedProject = [System.IO.Path]::GetFullPath($ProjectDir)
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedProject, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write outside project: $Path"
    }
}

Assert-InProject -Path $DistDir
Assert-InProject -Path $AppDir
Assert-InProject -Path $ZipPath

if (Test-Path $AppDir) {
    Remove-Item -LiteralPath $AppDir -Recurse -Force
}
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
New-Item -ItemType Directory -Path $AppDir -Force | Out-Null

$files = @(
    "app.py",
    "alerts.py",
    "atomic_io.py",
    "coach_providers.py",
    "cv_engine.py",
    "engine_status.py",
    "force_utf8.py",
    "gemini_coach.py",
    "groq_coach.py",
    "product_intelligence.py",
    "scoring.py",
    "security.py",
    "site_server.py",
    "tts_phrases.py",
    "requirements-local.txt",
    ".env.example",
    "Start-Auto.bat",
    "Start-Auto.ps1",
    "Start-Game-Overlay.bat",
    "Start-Game-Overlay.ps1",
    "Start-KinaestheticAI.bat",
    "Start-KinaestheticAI.ps1",
    "Start-KinaestheticAI-All.bat",
    "Start-KinaestheticAI-All.ps1",
    "Start-Site.bat",
    "Start-Site.ps1",
    "Keep-Overlay-TopMost.ps1",
    "Create-Game-Overlay-Shortcut.ps1"
)

foreach ($file in $files) {
    $source = Join-Path $ProjectDir $file
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $AppDir $file) -Force
    }
}

foreach ($dir in @("pitch_site", "models")) {
    $source = Join-Path $ProjectDir $dir
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $AppDir $dir) -Recurse -Force
    }
}

$docsOut = Join-Path $AppDir "docs"
New-Item -ItemType Directory -Path $docsOut -Force | Out-Null
foreach ($doc in @("docs\GAME_RECORDING_MODE.md", "docs\DESKTOP_APP_PLAN.md")) {
    $source = Join-Path $ProjectDir $doc
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $docsOut -Force
    }
}

New-Item -ItemType Directory -Path (Join-Path $AppDir "data") -Force | Out-Null
Set-Content -Path (Join-Path $AppDir "data\.gitkeep") -Value "" -Encoding UTF8

Compress-Archive -LiteralPath $AppDir -DestinationPath $ZipPath -Force

Write-Host "Portable app folder: $AppDir" -ForegroundColor Green
Write-Host "Portable app zip:    $ZipPath" -ForegroundColor Green
Write-Host ""
Write-Host "Recommended tester entrypoint:" -ForegroundColor Cyan
Write-Host "  Start-Game-Overlay.bat"
