@echo off
REM Traegt JARVIS in den Autostart ein, damit sich der PC nach dem Anmelden
REM von selbst mit dem Server koppelt. Zum Ausschalten:  python autostart.py aus
cd /d "%~dp0"
python autostart.py an
echo.
pause
