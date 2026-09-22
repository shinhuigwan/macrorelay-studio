@echo off
chcp 65001 >nul
title MacroRelay Studio 자동 설치
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Launch
if errorlevel 1 (
  echo.
  echo 설치에 실패했습니다. bootstrap-install-error.txt 파일을 확인하세요.
  pause
)

