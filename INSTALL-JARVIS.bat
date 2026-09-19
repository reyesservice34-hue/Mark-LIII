@echo off
REM JARVIS auf diesem Windows-Rechner einrichten.
REM Doppelklicken. Mehr ist nicht zu tun.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python ist nicht installiert oder nicht im PATH.
    echo.
    echo   1. https://www.python.org/downloads/ oeffnen
    echo   2. Herunterladen und installieren
    echo   3. WICHTIG: beim Installieren "Add python.exe to PATH" ankreuzen
    echo   4. Danach dieses Fenster schliessen und die Datei erneut doppelklicken
    echo.
    pause
    exit /b 1
)

python install_desktop.py
echo.
pause
