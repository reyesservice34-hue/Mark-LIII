#!/usr/bin/env bash
# Verbindet MIA mit den bereits kopierten Hermes-Daten (/data/hermes im Volume)
# und stellt die Hermes-Werkzeugbrücke auf den Container selbst um.
# NICHT automatisch ausgeführt. Vorher Backup prüfen, dann von Hand starten:
#   bash command_center/scripts/integrate_hermes_memory.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

TS=$(date -u +%Y%m%dT%H%M%SZ)
cp -p docker-compose.command-center.yml "docker-compose.command-center.yml.backup-hermes-$TS"

# 1) Hermes-Werkzeugbinaries nur lesend in den Container einbinden
python3 - <<'PY'
p = "docker-compose.command-center.yml"
s = open(p).read()
if "/data/hermes/tools" not in s:
    anchor = "    volumes:\n"
    i = s.index("jarvis-command-center:")
    j = s.index(anchor, i) + len(anchor)
    ins = ("      # Hermes-Werkzeugbinaries (chromium, ffmpeg, ripgrep, node, uv), nur lesen.\n"
           "      - /root/Mark-LIII/hermes/tools:/data/hermes/tools:ro\n")
    s = s[:j] + ins + s[j:]
    open(p, "w").write(s)
    print("compose: Werkzeug-Mount ergaenzt")
PY

# 2) MCP-Eintrag auf den Container umstellen, Genehmigung pro Aufruf aus
docker exec jarvis-command-center python -c "
import sqlite3
c = sqlite3.connect('/data/jarvis.db')
n = c.execute(\"update mcp_servers set url='http://127.0.0.1:8765/mcp', requires_approval=0 where slug='hermes_tools'\").rowcount
c.commit(); print('mcp rows updated:', n)"

# 3) Container mit der geaenderten Compose-Datei neu erstellen
docker compose -f docker-compose.command-center.yml up -d jarvis-command-center
echo "fertig. Backup: docker-compose.command-center.yml.backup-hermes-$TS"
