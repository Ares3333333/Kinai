#requires -version 5.1
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalLauncher = Join-Path $ProjectDir "Start-Game-Overlay.ps1"
$OnlineLauncher = Join-Path $ProjectDir "Start-Online-Game-Overlay.ps1"
$Desktop = [Environment]::GetFolderPath("Desktop")
$LocalShortcutPath = Join-Path $Desktop "Kinaesthetic AI - Game Recording.lnk"
$OnlineShortcutPath = Join-Path $Desktop "Kinaesthetic AI - Online Recording.lnk"

if (-not (Test-Path $LocalLauncher)) {
    throw "Launcher not found: $LocalLauncher"
}
if (-not (Test-Path $OnlineLauncher)) {
    throw "Launcher not found: $OnlineLauncher"
}

$shell = New-Object -ComObject WScript.Shell

$shortcut = $shell.CreateShortcut($LocalShortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$LocalLauncher`""
$shortcut.WorkingDirectory = $ProjectDir
$shortcut.Description = "Start local Kinaesthetic AI game recording mode: /play plus small voice overlay."
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
$shortcut.Save()

$shortcut = $shell.CreateShortcut($OnlineShortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$OnlineLauncher`""
$shortcut.WorkingDirectory = $ProjectDir
$shortcut.Description = "Start deployed HTTPS Kinaesthetic AI game recording mode."
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
$shortcut.Save()

Write-Host "Shortcut created: $LocalShortcutPath" -ForegroundColor Green
Write-Host "Shortcut created: $OnlineShortcutPath" -ForegroundColor Green
