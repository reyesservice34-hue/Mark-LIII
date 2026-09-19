@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title JARVIS installieren

REM ============================================================
REM  JARVIS auf diesem Windows-Rechner einrichten.
REM  Doppelklicken. Danach liegt "JARVIS" auf dem Desktop und im
REM  Startmenue: ein eigenes Fenster mit genau demselben Dashboard
REM  wie im Browser, direkt verbunden mit deinem Server.
REM ============================================================
set "JARVIS_URL=https://jarvis.jarvis-reyes.de"

echo.
echo   JARVIS wird eingerichtet ...
echo.

REM --- Browser suchen: Chrome bevorzugt (Spracherkennung), sonst Edge
set "BROWSER="
for %%P in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
) do (
  if not defined BROWSER if exist %%P set "BROWSER=%%~P"
)

if not defined BROWSER (
  echo   Weder Google Chrome noch Microsoft Edge gefunden.
  echo   Bitte Chrome installieren: https://www.google.com/chrome
  echo.
  pause
  exit /b 1
)
echo   Browser gefunden: %BROWSER%

REM --- Verknuepfungen anlegen (Desktop + Startmenue)
set "PS=%TEMP%\jarvis-shortcut.ps1"
> "%PS%" echo $ws = New-Object -ComObject WScript.Shell
>> "%PS%" echo $targets = @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))
>> "%PS%" echo foreach ($d in $targets) {
>> "%PS%" echo   $s = $ws.CreateShortcut((Join-Path $d 'JARVIS.lnk'))
>> "%PS%" echo   $s.TargetPath = '%BROWSER%'
>> "%PS%" echo   $s.Arguments = '--app=%JARVIS_URL% --window-size=1500,950'
>> "%PS%" echo   $s.IconLocation = '%BROWSER%,0'
>> "%PS%" echo   $s.Description = 'JARVIS Command Center'
>> "%PS%" echo   $s.Save()
>> "%PS%" echo }
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS%"
del "%PS%" >nul 2>nul

if errorlevel 1 (
  echo.
  echo   Die Verknuepfung konnte nicht angelegt werden.
  pause
  exit /b 1
)

echo.
echo   Fertig. "JARVIS" liegt jetzt auf dem Desktop und im Startmenue.
echo.
echo   Beim ersten Start:
echo     1. Mit deinem normalen JARVIS-Zugang anmelden.
echo     2. Das Mikrofon einmal erlauben (fuer das Live-Gespraech).
echo.
choice /c JN /n /m "  JARVIS jetzt starten? (J/N) "
if errorlevel 2 goto ende
start "" "%BROWSER%" --app=%JARVIS_URL% --window-size=1500,950

:ende
echo.
echo   Tipp: Willst du, dass JARVIS zusaetzlich deinen PC steuern kann
echo   (Programme oeffnen, Dateien, ...)? Im Dashboard unter "Geraete"
echo   auf "Rechner koppeln" gehen und den dort gezeigten Befehl in
echo   PowerShell einfuegen.
echo.
pause
