@echo off
cd /d "%~dp0"
title OnmyojiPilot Console

rem Prefer the user's own Python (stable path), fall back to PATH.
set "PY=python"
if exist "D:\python\python.exe" set "PY=D:\python\python.exe"

if not exist "server.py" (
    echo [ERROR] server.py not found. Run this file inside the OnmyojiPilot folder.
    pause
    exit /b 1
)

echo Using Python: %PY%
echo.

"%PY%" server.py

if errorlevel 1 (
    echo.
    echo [ERROR] Server exited abnormally. The reason is printed above.
    echo If nothing above talks about the port, check numpy / opencv / pillow.
    echo.
    pause
)
