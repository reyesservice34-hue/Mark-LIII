#!/usr/bin/env bash
# Lexware-API-Schluessel eintragen und pruefen, damit die grauen Lexware-Werkzeuge des Buchhalters gruen werden.
# Aufruf:  bash /root/jarvis/command_center/setup-lexware.sh
# Der Schluessel wird beim Eintippen NICHT angezeigt und nirgends ausgegeben.
set -euo pipefail
ENV=/root/jarvis/command_center/.env
echo "Schluessel anlegen unter https://app.lexware.de/addons/public-api (im Browser, bei Lexware angemeldet), dann hier einfuegen."
read -r -s -p "API-Schluessel: " KEY; echo
[ -n "$KEY" ] || { echo "Kein Schluessel eingegeben."; exit 1; }
echo "Pruefe den Schluessel bei Lexware ..."
CODE=$(curl -s -o /tmp/lx-profile.json -w '%{http_code}' -H "Authorization: Bearer $KEY" -H "Accept: application/json" https://api.lexware.io/v1/profile || true)
if [ "$CODE" != "200" ]; then rm -f /tmp/lx-profile.json; echo "Lexware lehnt den Schluessel ab (HTTP $CODE). Nichts wurde geaendert."; exit 1; fi
NAME=$(python3 -c 'import json;d=json.load(open("/tmp/lx-profile.json"));print(d.get("companyName","?"))'); rm -f /tmp/lx-profile.json
echo "Schluessel gueltig. Firma in Lexware: $NAME"
cp -a "$ENV" "/root/aufbewahrt-2026-09-19/vor-migration/command-center.env.vor-lexware"
KEY="$KEY" python3 - <<'PY'
import os, re
p = "/root/jarvis/command_center/.env"; s = open(p).read(); k = os.environ["KEY"]
s = re.sub(r"(?m)^LEXWARE_API_KEY=.*$", "LEXWARE_API_KEY=" + k, s) if re.search(r"(?m)^LEXWARE_API_KEY=", s) else s.rstrip("\n") + "\nLEXWARE_API_KEY=" + k + "\n"
open(p, "w").write(s)
PY
chmod 600 "$ENV"
cd /root/jarvis && docker compose -f docker-compose.command-center.yml up -d --force-recreate jarvis-command-center >/dev/null 2>&1
sleep 25
docker ps --filter name=jarvis-command-center --format 'Jarvis: {{.Status}}'
echo "Fertig. Die Lexware-Werkzeuge muessten im Dashboard jetzt gruen sein (Seite neu laden)."
