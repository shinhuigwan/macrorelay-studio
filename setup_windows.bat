@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
if errorlevel 1 (
  echo.
  echo [MacroRelay] Installation failed. See the message above.
  pause
  exit /b 1
)
echo.
echo [MacroRelay] Installation completed. Starting Studio...
call "%~dp0run_studio.bat"
exit /b 0
