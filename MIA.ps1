param(
  [ValidateSet('start','stop','restart','status','repair','autostart-on','autostart-off')]
  [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Agent = Join-Path $Root 'desktop_agent.py'
$Check = Join-Path $Root 'check_connection.py'
$Auto = Join-Path $Root 'autostart.py'
$Wake = Join-Path $Root 'MIA-Wake.ps1'
$Req = Join-Path $Root 'requirements.txt'
function Py { $p = Get-Command python -ErrorAction SilentlyContinue; if (-not $p) { throw 'Python ist nicht im PATH.' }; $p.Source }
function AgentProcesses {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -like '*desktop_agent.py*' -and $_.CommandLine -like "*$Root*"
  }
}
function WakeProcesses {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -like '*MIA-Wake.ps1*' -and $_.CommandLine -like "*$Root*"
  }
}
function Start-MiaWake {
  if (-not (Test-Path $Wake)) { return }
  if (WakeProcesses) { return }
  Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$Wake`"") -WorkingDirectory $Root -WindowStyle Hidden
}
function Stop-MiaWake {
  @(WakeProcesses) | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Start-MiaBridge {
  if (AgentProcesses) { Start-MiaWake; Write-Host 'MIA Bridge laeuft bereits. Hey MIA ist aktiv.' -ForegroundColor Green; return }
  $python = Py
  $pyw = Join-Path (Split-Path $python) 'pythonw.exe'
  if (-not (Test-Path $pyw)) { $pyw = $python }
  Start-Process -FilePath $pyw -ArgumentList @('"'+$Agent+'"') -WorkingDirectory $Root -WindowStyle Hidden
  Start-Sleep -Seconds 2
  if (AgentProcesses) { Start-MiaWake; Write-Host 'MIA Bridge gestartet. Hey MIA ist aktiv.' -ForegroundColor Green } else { throw 'MIA Bridge konnte nicht gestartet werden.' }
}
function Stop-MiaBridge {
  $p = @(AgentProcesses)
  if ($p.Count) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
  Stop-MiaWake
  Write-Host 'MIA Bridge und Hey-MIA-Listener gestoppt.' -ForegroundColor Yellow
}
function Show-MiaStatus {
  Write-Host ''
  Write-Host '=== MIA Windows Bridge ===' -ForegroundColor Cyan
  Write-Host ('Pfad:       ' + $Root)
  Write-Host ('Bridge:     ' + ($(if (AgentProcesses) {'ONLINE'} else {'OFFLINE'})))
  Write-Host ('Hey MIA:    ' + ($(if (WakeProcesses) {'LISTENING'} else {'OFFLINE'})))
  if (Test-Path $Auto) { & (Py) $Auto }
  if (Test-Path $Check) { Write-Host ''; & (Py) $Check }
}
function Repair-MiaBridge {
  $python = Py
  Write-Host 'Pruefe Abhaengigkeiten ...'
  if (Test-Path $Req) { & $python -m pip install --quiet -r $Req }
  if (Test-Path $Check) { & $python $Check }
  Stop-MiaBridge
  Start-MiaBridge
  Write-Host 'Repair abgeschlossen.' -ForegroundColor Green
}
switch ($Action) {
  'start' { Start-MiaBridge }
  'stop' { Stop-MiaBridge }
  'restart' { Stop-MiaBridge; Start-MiaBridge }
  'status' { Show-MiaStatus }
  'repair' { Repair-MiaBridge }
  'autostart-on' { & (Py) $Auto an }
  'autostart-off' { & (Py) $Auto aus }
}
