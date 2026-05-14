#requires -version 5.1
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectDir "Start-Game-Overlay.bat"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Kinaesthetic AI - Game Recording.lnk"

if (-not (Test-Path $Launcher)) {
    throw "Launcher not found: $Launcher"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $Launcher
$shortcut.WorkingDirectory = $ProjectDir
$shortcut.Description = "Start Kinaesthetic AI game recording mode: /play plus small voice overlay."
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,264"
$shortcut.Save()

Write-Host "Shortcut created: $ShortcutPath" -ForegroundColor Green
