@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 (
        py -3.11 launch.py %*
        goto finished
    )
)
where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.11 from https://www.python.org/downloads/
    echo During installation, enable Add python.exe to PATH.
    pause
    exit /b 1
)
python launch.py %*
:finished
set "launch_status=%errorlevel%"
if not "%launch_status%"=="0" pause
endlocal & exit /b %launch_status%
