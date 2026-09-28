@echo off
REM JARVIS — die Desktop-App.
REM Vorher einmal INSTALL-JARVIS.bat ausfuehren.
cd /d "%~dp0"
title JARVIS

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python fehlt. Erst INSTALL-JARVIS.bat doppelklicken.
    echo.
    pause
    exit /b 1
)

REM pythonw statt python: kein schwarzes Fenster hinter der App.
where pythonw >nul 2>nul
if errorlevel 1 (
    python jarvis_desktop.py
    if errorlevel 1 pause
) else (
    start "" pythonw jarvis_desktop.py
)
