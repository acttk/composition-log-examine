@echo off
title Nightwatch - Log Analysis
cd /d "%~dp0"

echo --------------------------------------
echo   Nightwatch - Log Analysis
echo   http://127.0.0.1:8000
echo --------------------------------------
echo.

:: If already running, just open the page (double filter: port AND LISTENING)
netstat -ano | findstr /C:":8000 " | findstr /C:"LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [INFO] Server already running, opening page...
    start "" "http://127.0.0.1:8000"
    timeout /t 2 >nul
    exit /b 0
)

:: Open browser after 2s in background
start "" cmd /c "timeout /t 2 >nul & start "" http://127.0.0.1:8000"

echo [START] Launching backend server...
echo [TIP]   Close this window to stop the server.
echo.
python -m uvicorn main:app --host 127.0.0.1 --port 8000
