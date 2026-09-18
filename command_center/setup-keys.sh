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
      *) warn "Das sieht nicht aus wie $shape."
         warn "Nochmal einfügen, oder nur Enter zum Überspringen." ;;
    esac
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
ask N8N_BASE_URL      "n8n-ADRESSE (keine Schlüssel!)" "die URL, unter der n8n läuft, z. B. http://127.0.0.1:5678" \
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
if ! $COMPOSE -f docker-compose.command-center.yml up -d --force-recreate; then
  warn "Der Neustart ist fehlgeschlagen — die Schlüssel stehen aber schon in $ENV_FILE."
  warn "Von Hand:  $COMPOSE -f docker-compose.command-center.yml up -d --force-recreate"
  exit 1
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
  warn "Log ansehen:  $COMPOSE -f docker-compose.command-center.yml logs -f"
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
info "Der Server hört nur auf ${BIND} — von außen ist absichtlich nichts offen."
info "Am schnellsten siehst du es über einen SSH-Tunnel. Auf deinem PC, in PowerShell:"
info ""
info "    ssh -L ${PORT}:127.0.0.1:${PORT} ${USER:-root}@${SERVER_IP}"
info ""
info "Das Fenster offen lassen, dann im Browser:  http://localhost:${PORT}"
info ""
info "Dauerhaft über eine Domain: A-Record auf ${SERVER_IP}, dann Reverse Proxy auf ${BIND}:${PORT}."
bold ""
