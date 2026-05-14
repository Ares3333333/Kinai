@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Online-Game-Overlay.ps1"
if errorlevel 1 pause
