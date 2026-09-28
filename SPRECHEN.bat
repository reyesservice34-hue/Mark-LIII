@echo off
REM Mit JARVIS sprechen — offene Leitung, kein Browser.
REM Vorher einmal INSTALL-JARVIS.bat ausfuehren.
cd /d "%~dp0"
title JARVIS - Sprache

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python fehlt. Erst INSTALL-JARVIS.bat doppelklicken.
    echo.
    pause
    exit /b 1
)

python desktop_voice.py
echo.
pause
