#!/usr/bin/env bash
# Schlüssel eintragen, ohne Editor.
#
#   bash command_center/setup-keys.sh
#
# Fragt der Reihe nach jeden Schlüssel ab, schreibt ihn in command_center/.env
# und startet den Container neu. Leer lassen und Enter drücken heißt: den
# bisherigen Wert behalten. Es wird nichts gelöscht, was schon dasteht.
#
# Warum ein Skript und kein nano: nano verlangt Strg+X, dann y, dann Enter —
# und wer da einmal n drückt, hat nichts gespeichert, ohne es zu merken.
set -euo pipefail

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m%s\033[0m\n' "$*"; }
fail() { printf '  \033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# ── wo sind wir ──────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
ENV_FILE="command_center/.env"
[ -f "$ENV_FILE" ] || fail "$ENV_FILE gibt es nicht. Erst bash command_center/install.sh laufen lassen."
command -v python3 >/dev/null 2>&1 || fail "python3 fehlt. Installieren mit: apt install python3"

bold ""
bold "=== Schlüssel eintragen ==="
bold ""
info "Für jeden Eintrag: Wert einfügen und Enter."
info "Nichts eingeben und Enter  =  so lassen, wie es ist."
bold ""

# Wert setzen, ohne dass er je in der Prozessliste oder der History steht:
# er geht als Umgebungsvariable an python3, nicht als Argument.
set_value() {
  JARVIS_SET_KEY="$1" JARVIS_SET_VALUE="$2" python3 - "$ENV_FILE" <<'PY'
import os, sys
path, key, value = sys.argv[1], os.environ["JARVIS_SET_KEY"], os.environ["JARVIS_SET_VALUE"]
with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines()
done = False
for i, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[i] = f"{key}={value}"
        done = True
        break
if not done:
    lines.append(f"{key}={value}")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
PY
}

current() {
  sed -n "s|^$1=\(.*\)$|\1|p" "$ENV_FILE" | head -1
}

# Eingabe bleibt verdeckt. Ein sichtbarer Schlüssel landet sonst im
# Terminal-Rückblick, im Screenshot und in jedem Chat, in den das kopiert wird
# — und gilt damit als verbrannt. Die Rückmeldung zeigt nur Anfang, Ende und
# Länge; das reicht zum Erkennen und verrät nichts.
mask() {
  local v="$1"
  if [ "${#v}" -le 12 ]; then
    printf '%s… (%s Zeichen)' "${v:0:3}" "${#v}"
  else
    printf '%s…%s (%s Zeichen)' "${v:0:7}" "${v: -4}" "${#v}"
  fi
}

# Jedes Feld kennt seine Form. Der n8n-Schlüssel im Adressfeld ist genau der
# Fehler, der sonst erst beim Gesundheitscheck auffällt — und dann als
# „n8n antwortet nicht", was in die Irre führt.
ask() {
  local key="$1" label="$2" hint="$3" pattern="${4:-*}" shape="${5:-}" now value
  now="$(current "$key")"
  if [ -n "$now" ]; then
    printf '  \033[1m%s\033[0m  [gesetzt: %s]\n' "$label" "$(mask "$now")"
  else
    printf '  \033[1m%s\033[0m  [noch leer]\n' "$label"
  fi
  [ -n "$hint" ] && printf '     %s\n' "$hint"
  # Zwei Versuche mit Formprüfung, danach wird der Wert genommen, wie er ist.
  # Ein Feld, aus dem nur das exakt richtige Format herausführt, ist eine
  # Falle: wer den passenden Schlüssel gerade nicht hat, kommt nicht weiter.
  local tries=0
  while :; do
    printf '     > '
    IFS= read -rs value || value=""
    printf '\n'
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    if [ -z "$value" ]; then
      printf '     unverändert\n\n'
      return
    fi
    # shellcheck disable=SC2254  # das Muster soll als Glob wirken
    case "$value" in
      $pattern) break ;;
    esac
    tries=$((tries + 1))
    if [ "$tries" -ge 2 ]; then
      warn "Passt immer noch nicht zur erwarteten Form — wird trotzdem eingetragen."
      warn "Falls es der falsche Wert war: Skript einfach nochmal laufen lassen."
      break
    fi
    warn "Das sieht nicht aus wie $shape."
    warn "Nochmal einfügen — oder Enter drücken, ohne etwas einzufügen, zum Überspringen."
  done
  set_value "$key" "$value"
  printf '     \033[32meingetragen: %s\033[0m\n\n' "$(mask "$value")"
}

ask ANTHROPIC_API_KEY "Anthropic (Claude)" "console.anthropic.com/settings/keys" \
    'sk-ant-*' "ein Anthropic-Schlüssel (sk-ant-…)"
ask OPENAI_API_KEY    "OpenAI"             "platform.openai.com/api-keys" \
    'sk-*' "ein OpenAI-Schlüssel (sk-…)"
ask GEMINI_API_KEY    "Google Gemini"      "aistudio.google.com/apikey" \
    'AIza*' "ein Gemini-Schlüssel (AIza…). Ein AQ.… ist ein OAuth-Token und geht hier nicht"
ask COMPOSIO_API_KEY  "Composio"           "platform.composio.dev — Gmail, Slack, Notion über eine Anmeldung" \
    '*' ""
ask COMPOSIO_USER_ID  "Composio-Benutzer"  "frei wählbar, z. B. reyes — leer lassen heißt 'default'" \
    '*' ""
# NICHT 127.0.0.1: JARVIS läuft im Container, dessen 127.0.0.1 ist er selbst.
# Ein n8n, das auf dem Host als 127.0.0.1:5678 lauscht, ist von hier gar nicht
# erreichbar. Der Containername im gemeinsamen Docker-Netz ist der Weg.
N8N_HINT="der Containername im Docker-Netz, z. B. http://n8n:5678 — NICHT 127.0.0.1"
if command -v docker >/dev/null 2>&1; then
  N8N_CONTAINER="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -ix 'n8n' | head -1 || true)"
  [ -n "$N8N_CONTAINER" ] && N8N_HINT="gefunden: Container '$N8N_CONTAINER' → http://${N8N_CONTAINER}:5678 (NICHT 127.0.0.1)"
fi
ask N8N_BASE_URL      "n8n-ADRESSE (keine Schlüssel!)" "$N8N_HINT" \
    'http*' "eine Adresse. Sie muss mit http:// oder https:// anfangen"
ask N8N_API_KEY       "n8n-Schlüssel"      "in n8n unter Einstellungen → API" \
    '*' ""

# ── welcher Anbieter denkt ───────────────────────────────────────────────
# Ohne Festlegung nimmt der Server Anthropic zuerst, dann OpenAI, dann Gemini.
PROVIDER="$(current JARVIS_AI_PROVIDER)"
bold "  Wer soll denken?"
info "1 = Anthropic   2 = OpenAI   3 = Gemini   Enter = automatisch wählen lassen"
[ -n "$PROVIDER" ] && info "aktuell: $PROVIDER"
printf '     > '
IFS= read -r CHOICE || CHOICE=""
case "${CHOICE// /}" in
  1) set_value JARVIS_AI_PROVIDER anthropic; info "Anthropic" ;;
  2) set_value JARVIS_AI_PROVIDER openai;    info "OpenAI" ;;
  3) set_value JARVIS_AI_PROVIDER gemini;    info "Gemini" ;;
  "") info "automatisch" ;;
  *) warn "Nicht verstanden — bleibt, wie es war." ;;
esac

# ── Nachbar-Container erreichbar machen ──────────────────────────────────
# Zeigt N8N_BASE_URL auf einen Containernamen, müssen beide Container im
# selben Docker-Netz sein, sonst löst der Name nicht auf. Das gehört in die
# .env und nicht in ein "docker network connect": letzteres hängt am
# laufenden Container und ist nach dem nächsten --force-recreate wieder weg.
N8N_URL="$(current N8N_BASE_URL)"
N8N_HOST="$(printf '%s' "$N8N_URL" | sed -n 's|^https\?://\([^:/]*\).*|\1|p')"
if [ "$N8N_HOST" = "127.0.0.1" ] || [ "$N8N_HOST" = "localhost" ]; then
  warn "N8N_BASE_URL zeigt auf $N8N_HOST. Aus dem Container heraus ist das er"
  warn "selbst, nicht dein Server — n8n wird so nie erreicht. Nimm den"
  warn "Containernamen, z. B. http://n8n:5678."
elif [ -n "$N8N_HOST" ] && docker inspect "$N8N_HOST" >/dev/null 2>&1; then
  NET="$(docker inspect "$N8N_HOST" \
         --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null \
         | awk '{print $1}')"
  if [ -n "$NET" ]; then
    set_value JARVIS_CC_SHARED_NETWORK "$NET"
    info "Docker-Netz von '$N8N_HOST' eingetragen: $NET"
  else
    warn "Konnte das Docker-Netz von '$N8N_HOST' nicht ermitteln."
  fi
fi

chmod 600 "$ENV_FILE"

# ── neu starten ──────────────────────────────────────────────────────────
bold ""
bold "  Starte neu …"
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  fail "docker compose fehlt — die .env ist geschrieben, starte selbst neu."
fi
if ! $COMPOSE --env-file command_center/.env -f docker-compose.command-center.yml up -d --force-recreate; then
  warn "Der Neustart ist fehlgeschlagen — die Schlüssel stehen aber schon in $ENV_FILE."
  warn "Von Hand:  $COMPOSE --env-file command_center/.env -f docker-compose.command-center.yml up -d --force-recreate"
  exit 1
fi

# ── nachsehen, ob n8n jetzt wirklich antwortet ───────────────────────────
# Behaupten reicht nicht: der Container fragt selbst nach. Jede HTTP-Antwort
# ist gut, auch 401 — sie beweist, dass die Verbindung steht.
if [ -n "$N8N_HOST" ] && [ "$N8N_HOST" != "127.0.0.1" ] && [ "$N8N_HOST" != "localhost" ]; then
  CODE="$(docker exec jarvis-command-center \
          curl -s -o /dev/null -m 8 -w '%{http_code}' "$N8N_URL" 2>/dev/null || echo 000)"
  if [ "$CODE" != "000" ]; then
    info "n8n antwortet aus dem Container heraus (HTTP $CODE)."
  else
    warn "n8n ist unter $N8N_URL noch nicht erreichbar."
    warn "Läuft der Container? Stimmt der Port? Steht JARVIS_CC_SHARED_NETWORK richtig?"
  fi
fi

PORT="$(sed -n 's|^JARVIS_CC_PORT=\([^#]*\).*|\1|p' "$ENV_FILE" | tr -d '[:space:]' | head -1)"
PORT="${PORT:-8080}"
BIND="$(sed -n 's|^JARVIS_CC_BIND=\([^#]*\).*|\1|p' "$ENV_FILE" | tr -d '[:space:]' | head -1)"
BIND="${BIND:-127.0.0.1}"
HEALTH="http://${BIND}:${PORT}/api/health"

printf '  Warte auf den Start '
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH" >/dev/null 2>&1; then OK=1; break; fi
  printf '.'
  sleep 2
done
printf '\n'

bold ""
if [ "${OK:-0}" != "1" ]; then
  warn "Er antwortet noch nicht auf $HEALTH."
  warn "Log ansehen:  $COMPOSE --env-file command_center/.env -f docker-compose.command-center.yml logs -f"
  exit 1
fi

BODY="$(curl -fsS "$HEALTH")"
STATUS="$(printf '%s' "$BODY" | sed -n 's/^{"status":"\([a-z_]*\)".*/\1/p' | head -1)"
MODE="$(printf '%s' "$BODY" | sed -n 's/.*"mode":"\([a-z_]*\)".*/\1/p' | head -1)"

bold "  Gesamtstatus: ${STATUS:-?}      Master Agent: ${MODE:-?}"
bold ""
case "$MODE" in
  local)  info "Der Master Agent denkt. Alles bereit." ;;
  remote) info "Der Master Agent hängt an einer vorgelagerten Control Plane." ;;
  *)      warn "Der Master Agent ist aus: es ist kein AI-Schlüssel angekommen."
          warn "Skript nochmal laufen lassen und beim ersten Eintrag einfügen." ;;
esac

# Die eigene öffentliche Adresse für den Tunnel-Befehl, damit sie nicht
# abgetippt werden muss. Ohne Netz bleibt der Platzhalter stehen.
SERVER_IP="$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || true)"
if [ -z "$SERVER_IP" ]; then
  SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
SERVER_IP="${SERVER_IP:-DEINE-SERVER-IP}"

bold ""
bold "  Dashboard ansehen"
if [ "$BIND" = "0.0.0.0" ]; then
  # Nicht beschönigen: bei 0.0.0.0 ist der Port offen, und ohne Proxy läuft
  # die Anmeldung unverschlüsselt über das Netz.
  info "Im Browser:  http://${SERVER_IP}:${PORT}"
  info ""
  warn "JARVIS_CC_BIND steht auf 0.0.0.0 — der Port ist offen, sofern die Firewall"
  warn "ihn durchlässt, und die Anmeldung läuft unverschlüsselt. Als Zwischenlösung"
  warn "in Ordnung; dauerhaft gehört ein Reverse Proxy mit Zertifikat davor und"
  warn "JARVIS_CC_BIND zurück auf 127.0.0.1."
else
  info "Der Server hört nur auf ${BIND}, von außen kommt niemand direkt dran."
  info "Am schnellsten siehst du ihn über einen SSH-Tunnel — auf DEINEM PC, in PowerShell,"
  info "nicht in diesem Fenster hier:"
  info ""
  info "    ssh -L ${PORT}:127.0.0.1:${PORT} ${USER:-root}@${SERVER_IP}"
  info ""
  info "Das Fenster offen lassen, dann im Browser:  http://localhost:${PORT}"
fi
info ""
info "Dauerhaft über eine Domain: A-Record auf ${SERVER_IP}, dann Reverse Proxy auf 127.0.0.1:${PORT}."
bold ""
