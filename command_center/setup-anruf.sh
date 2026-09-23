#!/usr/bin/env bash
# Anruf bei Dringendem einrichten (Twilio). Jarvis ruft dann DEINE Nummer an und liest die Meldung vor.
# Vorher bei twilio.com ein Konto anlegen, eine Telefonnummer mit Sprachfunktion kaufen (Account SID und Auth Token stehen auf der Startseite der Konsole).
# Aufruf:  bash /root/jarvis/command_center/setup-anruf.sh      Der Auth Token wird beim Eintippen NICHT angezeigt.
set -euo pipefail
ENV=/root/jarvis/command_center/.env
read -r -p "Account SID (beginnt mit AC): " SID
read -r -s -p "Auth Token: " TOK; echo
read -r -p "Twilio-Nummer, von der angerufen wird (z. B. +4930...): " FROM
read -r -p "Deine Handynummer, die klingeln soll (z. B. +49170...): " TO
[ -n "$SID" ] && [ -n "$TOK" ] && [ -n "$FROM" ] && [ -n "$TO" ] || { echo "Es fehlt eine Angabe. Nichts wurde geaendert."; exit 1; }
CODE=$(curl -s -o /dev/null -w '%{http_code}' -u "$SID:$TOK" "https://api.twilio.com/2010-04-01/Accounts/$SID.json" || true)
[ "$CODE" = "200" ] || { echo "Twilio lehnt die Zugangsdaten ab (HTTP $CODE). Nichts wurde geaendert."; exit 1; }
echo "Zugangsdaten gueltig."
cp -a "$ENV" "/root/aufbewahrt-2026-09-19/vor-migration/command-center.env.vor-anruf"
SID="$SID" TOK="$TOK" FROM="$FROM" TO="$TO" python3 - <<'PY'
import os, re
p = "/root/jarvis/command_center/.env"; s = open(p).read()
for k, e in (("TWILIO_ACCOUNT_SID", "SID"), ("TWILIO_AUTH_TOKEN", "TOK"), ("TWILIO_FROM", "FROM"), ("JARVIS_CC_CALL_TO", "TO")):
    line = f"{k}={os.environ[e]}"
    s = re.sub(rf"(?m)^{k}=.*$", lambda m: line, s) if re.search(rf"(?m)^{k}=", s) else s.rstrip("\n") + "\n" + line + "\n"
open(p, "w").write(s)
PY
chmod 600 "$ENV"
cd /root/jarvis && docker compose -f docker-compose.command-center.yml up -d --force-recreate jarvis-command-center >/dev/null 2>&1
sleep 25
docker ps --filter name=jarvis-command-center --format 'Jarvis: {{.Status}}'
echo "Fertig. In der App unter Meldungen kannst du jetzt einen Testanruf ausloesen."
