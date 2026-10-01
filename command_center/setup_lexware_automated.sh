#!/usr/bin/env bash
# Lexware-API-Schluessel eintragen und pruefen
set -euo pipefail
ENV=/root/Mark-LIII/command_center/.env
KEY="<<GENERATED_KEY_PLACEHOLDER>>" # Hier waere der Schluessel im Prozess eingesetzt worden

echo "Pruefe den Schluessel bei Lexware ..."
CODE=$(curl -s -o /tmp/lx-profile.json -w '%{http_code}' -H "Authorization: Bearer $KEY" -H "Accept: application/json" https://api.lexware.io/v1/profile || true)

if [ "$CODE" != "200" ]; then 
  rm -f /tmp/lx-profile.json
  echo "Lexware lehnt den Schluessel ab (HTTP $CODE). Nichts wurde geaendert."
  exit 1
fi

python3 -c 'import json;d=json.load(open("/tmp/lx-profile.json"));print("Schluessel gueltig. Firma: " + d.get("companyName","?"))'; rm -f /tmp/lx-profile.json

# In .env schreiben
sed -i "s|^LEXWARE_API_KEY=.*$|LEXWARE_API_KEY=$KEY|" "$ENV" || echo "LEXWARE_API_KEY=$KEY" >> "$ENV"

# Container neu starten
cd /root/Mark-LIII && docker compose -f docker-compose.command-center.yml up -d --force-recreate jarvis-command-center >/dev/null 2>&1
echo "Lexware konfiguriert und Command Center neu gestartet."
