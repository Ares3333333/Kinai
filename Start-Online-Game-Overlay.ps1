#requires -version 5.1
<#
.SYNOPSIS
  Starts game recording mode against a deployed HTTPS site.
#>
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectDir "Start-Game-Overlay.ps1"

$url = $env:KAI_SITE_URL
if (-not $url) {
    $stored = [Environment]::GetEnvironmentVariable("KAI_SITE_URL", "User")
    if ($stored) { $url = $stored }
}
if (-not $url) {
    $url = Read-Host "Paste deployed HTTPS site URL, for example https://kinaesthetic.example.com"
}
$url = $url.Trim().TrimEnd("/")

if ($url -notmatch "^https://") {
    Write-Host "Public camera mode needs HTTPS. URL must start with https://." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

[Environment]::SetEnvironmentVariable("KAI_SITE_URL", $url, "User")
& $Launcher -SiteUrl $url
