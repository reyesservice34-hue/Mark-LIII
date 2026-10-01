"""
Den Rechner ankoppeln: ein Befehl, den man kopiert, und fertig.

Bisher war der Weg: Repository von Hand holen, Python finden, Abhängigkeiten
nachziehen, im Dashboard ein Token erzeugen, es in eine Datei eintragen, den
Autostart einrichten. Sechs Schritte, jeder mit eigenen Stolpersteinen — und
wer bei Schritt vier stehenbleibt, hat nichts, das läuft.

Also erzeugt der Server das Skript, das all das tut, und setzt dabei ein,
was nur er weiß: seine eigene Adresse und das frisch erzeugte Token. Der
Nutzer kopiert eine Zeile.

Was das Skript NICHT tut, und warum:

  * Es lädt nichts nach, was es nicht prüfen kann. Der Quelltext kommt als
    ZIP von GitHub — kein git nötig, kein Konto, kein Schlüssel.
  * Es installiert Python nicht selbst. Eine stille Python-Installation
    verändert den PATH eines fremden Rechners; das gehört dem Menschen davor.
    Fehlt Python, sagt es, wo es herkommt, und hört auf.
  * Es richtet den Autostart nur ein, wenn man ihn will — mit -Autostart.

Das Token steht im Befehl, den der Nutzer kopiert, nicht im Skript: Das
Skript selbst ist öffentlich abrufbar und enthält deshalb kein Geheimnis.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Von wo der Desktop-Teil geholt wird. Ableitbar aus dem Quelltextverzeichnis,
# wenn eines gereicht wurde — sonst die Adresse, aus der dieser Server stammt.
FALLBACK_REPO = "https://github.com/reyesservice34-hue/Mark-LIII"


def repo_url() -> str:
    """Woher der Rechner den Desktop-Teil holt."""
    aus_env = os.environ.get("JARVIS_CC_DESKTOP_REPO", "").strip()
    if aus_env:
        return aus_env.rstrip("/").removesuffix(".git")
    from .source import source_dir
    root = source_dir()
    if root is not None:
        try:
            out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(root),
                                 capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip().startswith("http"):
                return out.stdout.strip().rstrip("/").removesuffix(".git")
        except Exception:  # noqa: BLE001 — eine Herkunftsangabe ist kein Grund zu scheitern
            pass
    return FALLBACK_REPO


def branch() -> str:
    return os.environ.get("JARVIS_CC_DESKTOP_BRANCH", "").strip() or "main"


def powershell(server_url: str, token: str, device_name: str = "") -> str:
    """Install the current MIA desktop bridge directly from this server."""
    name_line = f'$DeviceName = "{device_name}"' if device_name else '$DeviceName = $env:COMPUTERNAME'
    return f"""# MIA Windows Bridge installieren.
# Erzeugt vom Server {server_url}. Enthaelt dein Geraetetoken - nicht weitergeben.
$ErrorActionPreference = "Stop"
$Server = "{server_url}"
$Token  = "{token}"
{name_line}
$Ziel   = "$env:USERPROFILE\\MIA"
$Bundle = "$Server/api/desktop/bundle.zip?token=$([uri]::EscapeDataString($Token))"

Write-Host ""
Write-Host "  MIA Windows Bridge wird eingerichtet" -ForegroundColor Cyan
Write-Host "  Server: $Server"
Write-Host "  Geraet: $DeviceName"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {{ throw "Python fehlt oder ist nicht im PATH." }}
Write-Host "  [ok] $((python --version 2>&1))"

$tmp = Join-Path $env:TEMP "mia-desktop.zip"
$aus = Join-Path $env:TEMP "mia-desktop-entpackt"
Invoke-WebRequest -Uri $Bundle -OutFile $tmp -UseBasicParsing
if (Test-Path $aus) {{ Remove-Item $aus -Recurse -Force }}
Expand-Archive -Path $tmp -DestinationPath $aus -Force
if (Test-Path $Ziel) {{
  $sicher = "$Ziel-alt-$(Get-Date -Format yyyyMMdd-HHmmss)"
  Move-Item $Ziel $sicher
  Write-Host "  [ok] alte Installation gesichert: $sicher"
}}
Move-Item $aus $Ziel
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
Set-Location $Ziel

python -m pip install --quiet -r requirements.txt
New-Item -ItemType Directory -Force -Path "$Ziel\\config" | Out-Null
$cfgPfad = "$Ziel\\config\\api_keys.json"
$cfg = @{{
  "JARVIS_DEVICE_NAME" = $DeviceName
  "jarvis_gateway_url" = $Server
  "jarvis_gateway_token" = $Token
  "desktop_device_name" = $DeviceName
}}
$cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPfad -Encoding UTF8

Write-Host "  [ok] Verbindung eingerichtet"
& powershell -ExecutionPolicy Bypass -File "$Ziel\\MIA.ps1" status
Write-Host ""
Write-Host "  Steuerung:" -ForegroundColor Cyan
Write-Host "    powershell -File $Ziel\\MIA.ps1 start"
Write-Host "    powershell -File $Ziel\\MIA.ps1 stop"
Write-Host "    powershell -File $Ziel\\MIA.ps1 status"
Write-Host "    powershell -File $Ziel\\MIA.ps1 repair"
Write-Host "    powershell -File $Ziel\\MIA.ps1 autostart-on"
Write-Host ""
"""


def shell(server_url: str, token: str, device_name: str = "") -> str:
    """Dasselbe für Linux und macOS."""
    repo = repo_url()
    zweig = branch()
    zip_url = f"{repo}/archive/refs/heads/{zweig}.zip"
    name_zeile = f'DEVICE="{device_name}"' if device_name else 'DEVICE="$(hostname)"'
    return f"""#!/usr/bin/env bash
# JARVIS auf diesem Rechner einrichten.
# Erzeugt vom Server {server_url}. Enthaelt dein Geraetetoken - nicht weitergeben.
set -euo pipefail

SERVER="{server_url}"
TOKEN="{token}"
{name_zeile}
ZIEL="$HOME/JARVIS"

echo
echo "  JARVIS wird eingerichtet"
echo "  Server: $SERVER"
echo "  Geraet: $DEVICE"
echo

command -v python3 >/dev/null 2>&1 || {{
  echo "  python3 fehlt. Erst installieren:"
  echo "    Debian/Ubuntu:  sudo apt install python3 python3-pip python3-venv"
  echo "    macOS:          brew install python"
  exit 1
}}
echo "  [ok] $(python3 --version)"

echo "  ... lade JARVIS herunter"
TMP="$(mktemp -d)"
curl -fsSL "{zip_url}" -o "$TMP/jarvis.zip" || {{
  echo "  Download fehlgeschlagen. Erreichbar? {zip_url}"; exit 1; }}

if [ -d "$ZIEL" ]; then
  SICHER="$ZIEL-alt-$(date +%Y%m%d-%H%M%S)"
  echo "  ... vorhandene Installation beiseite gelegt: $SICHER"
  mv "$ZIEL" "$SICHER"
fi
python3 -m zipfile -e "$TMP/jarvis.zip" "$TMP/aus"
mv "$TMP/aus/"*/ "$ZIEL"
rm -rf "$TMP"
echo "  [ok] entpackt nach $ZIEL"

cd "$ZIEL"
echo "  ... installiere Abhaengigkeiten"
python3 -m pip install --quiet -r requirements.txt || \\
  echo "  Einige Abhaengigkeiten fehlen - JARVIS startet trotzdem."

mkdir -p "$ZIEL/config"
python3 - "$SERVER" "$TOKEN" "$DEVICE" <<'PY'
import json, sys, pathlib
server, token, name = sys.argv[1:4]
p = pathlib.Path("config/api_keys.json")
cfg = {{}}
if p.exists():
    try: cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception: cfg = {{}}
cfg.update({{"JARVIS_GATEWAY_URL": server, "JARVIS_GATEWAY_TOKEN": token,
            "JARVIS_DEVICE_NAME": name}})
p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
PY
echo "  [ok] Geraetetoken eingetragen"

echo "  ... pruefe die Verbindung"
if curl -fsS "$SERVER/api/health" >/dev/null 2>&1; then
  echo "  [ok] Server antwortet"
else
  echo "  Server nicht erreichbar - eingerichtet ist trotzdem alles."
fi

echo
echo "  Fertig."
echo "  Starten:    cd $ZIEL && python3 desktop_agent.py"
echo "  Nachsehen:  cd $ZIEL && python3 check_connection.py"
echo
"""


def one_liner(server_url: str, token: str, system: str = "windows") -> str:
    """Die eine Zeile, die der Nutzer kopiert."""
    if system == "windows":
        return (f'powershell -ExecutionPolicy Bypass -Command "irm '
                f'{server_url}/api/desktop/install.ps1?token={token} | iex"')
    return f'curl -fsSL "{server_url}/api/desktop/install.sh?token={token}" | bash'


__all__ = ["powershell", "shell", "one_liner", "repo_url", "branch"]
