@echo off
REM Prueft, ob dieser Rechner mit dem Server reden kann.
cd /d "%~dp0"
python check_connection.py
echo.
pause
