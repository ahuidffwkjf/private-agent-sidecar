@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Install Node.js 22.12 or later, then open this file again.
  pause
  exit /b 1
)
call npm ci --no-audit --no-fund
if errorlevel 1 (
  pause
  exit /b 1
)
call npm start
if errorlevel 1 pause
