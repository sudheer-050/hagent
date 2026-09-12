@echo off
setlocal

rem Only start the server if it isn't already running (port 8000 not in use).
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
    cd /d "%~dp0"
    start "Hagent" /B "C:\Users\gsudh\AppData\Local\Programs\Python\Python310\python.exe" -m uvicorn hagent.web:app --port 8000
)

rem Poll port 8000 until the server answers, up to ~30s, before opening the
rem browser -- opening before the server's ready just shows a connection error.
set _tries=0
:wait_for_server
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not errorlevel 1 goto server_ready
set /a _tries+=1
if %_tries% geq 30 goto server_ready
timeout /t 1 /nobreak >nul
goto wait_for_server
:server_ready

start "" "http://127.0.0.1:8000/"
