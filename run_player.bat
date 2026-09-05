@echo off
start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0run_player.ps1"
exit /b 0
