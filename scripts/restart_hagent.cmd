@echo off
setlocal
title Restart Hagent
"%~dp0..\.venv\Scripts\python.exe" "%~dp0restart_hagent.py"
if errorlevel 1 (
  echo.
  echo Hagent could not restart. Copy the error shown above.
  pause
  exit /b 1
)
echo.
echo Hagent is ready. Open http://127.0.0.1:8003/connections
pause
