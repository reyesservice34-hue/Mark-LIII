#!/usr/bin/env bash
# JARVIS Command Center — Installation auf deinem Server.
#
#   curl -fsSL https://raw.githubusercontent.com/reyesservice34-hue/Mark-LIII/claude/agency-agent-installation-ransfo/command_center/install.sh | bash
#
# oder, wenn das Repository schon da ist:
#
#   bash command_center/install.sh
#
# Das Skript ist absichtlich langweilig: es prüft erst alles, ändert nichts an
# vorhandenen Diensten und legt nur an, was noch fehlt. Ein zweiter Lauf
# aktualisiert bloß und lässt deine .env in Ruhe.
set -euo pipefail

REPO_URL="${JARVIS_REPO_URL:-https://github.com/reyesservice34-hue/Mark-LIII.git}"
BRANCH="${JARVIS_BRANCH:-claude/agency-agent-installation-ransfo}"
TARGET="${JARVIS_DIR:-$HOME/jarvis}"
PORT="${JARVIS_CC_PORT:-8080}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m%s\033[0m\n' "$*"; }
fail() { printf '  \033[31m%s\033[0m\n' "$*" >&2; exit 1; }

bold ""
bold "=== JARVIS Command Center — Installation ==="
bold ""

# ── 1. Voraussetzungen ───────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || fail "git fehlt. Installieren mit: apt install git"
command -v docker >/dev/null 2>&1 || fail "docker fehlt. Anleitung: https://docs.docker.com/engine/install/"

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  fail "docker compose fehlt. Installieren mit: apt install docker-compose-plugin"
fi
docker info >/dev/null 2>&1 || fail "Der Docker-Dienst läuft nicht oder dein Benutzer darf ihn nicht nutzen.
     Starten mit: sudo systemctl start docker
     Rechte:      sudo usermod -aG docker \$USER   (danach neu anmelden)"

info "git, docker und ${COMPOSE} sind da."

# ── 2. Code holen ────────────────────────────────────────────────────────
if [ -d "$TARGET/.git" ]; then
  info "Aktualisiere $TARGET …"
  git -C "$TARGET" fetch origin "$BRANCH" --quiet
  git -C "$TARGET" checkout "$BRANCH" --quiet
  git -C "$TARGET" pull --ff-only origin "$BRANCH" --quiet \
    || warn "git pull kam nicht durch (lokale Änderungen?) — es läuft der Stand, der da ist."
else
  info "Hole den Code nach $TARGET …"
  git clone --branch "$BRANCH" --depth 20 "$REPO_URL" "$TARGET" --quiet
fi
cd "$TARGET"
info "Stand: $(git log --oneline -1)"

# ── 3. Konfiguration ─────────────────────────────────────────────────────
ENV_FILE="command_center/.env"
GENERATED_PW=""
if [ -f "$ENV_FILE" ]; then
  info "command_center/.env ist vorhanden und wird nicht angefasst."
else
  cp command_center/.env.example "$ENV_FILE"
  GENERATED_PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)"
  SECRET="$(head -c 48 /dev/urandom | base64 | tr -d '/+=' | head -c 60)"
  # BSD- und GNU-sed vertragen sich nicht bei -i, deshalb über eine Temp-Datei.
  tmp="$(mktemp)"
  sed -e "s|^JARVIS_CC_ADMIN_PASSWORD=.*|JARVIS_CC_ADMIN_PASSWORD=${GENERATED_PW}|" \
      -e "s|^JARVIS_CC_SECRET_KEY=.*|JARVIS_CC_SECRET_KEY=${SECRET}|" \
      "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  info "command_center/.env angelegt, Admin-Passwort erzeugt."
fi

# ── 4. Bauen und starten ─────────────────────────────────────────────────
info "Baue das Image (beim ersten Mal dauert das ein paar Minuten) …"
$COMPOSE -f docker-compose.command-center.yml up -d --build

# ── 5. Warten, bis er antwortet ──────────────────────────────────────────
info "Warte auf den Start …"
# Wert robust lesen: ohne etwaigen Kommentar dahinter und ohne Leerzeichen.
BIND="$(sed -n 's/^JARVIS_CC_BIND=\([^#]*\).*/\1/p' "$ENV_FILE" | tr -d '[:space:]' | head -1)"
BIND="${BIND:-127.0.0.1}"
HEALTH="http://${BIND}:${PORT}/api/health"
for i in $(seq 1 60); do
  if curl -fsS "$HEALTH" >/dev/null 2>&1; then
    OK=1; break
  fi
  sleep 2
done

if [ "${OK:-0}" != "1" ]; then
  warn "Er antwortet noch nicht auf $HEALTH."
  warn "Log ansehen mit:  $COMPOSE -f docker-compose.command-center.yml logs -f"
  exit 1
fi

# Die Gesamtbewertung steht ganz vorn in der Antwort ({"status":"…","components":…).
# Ohne den Anker am Zeilenanfang würde das letzte "status" einer Komponente gewinnen.
BODY="$(curl -fsS "$HEALTH")"
STATUS="$(printf '%s' "$BODY" | sed -n 's/^{"status":"\([a-z_]*\)".*/\1/p' | head -1)"
bold ""
bold "  Läuft. Health: ${STATUS:-ok}"
bold ""
if [ "$STATUS" = "degraded" ]; then
  info "\"degraded\" heißt: der Server läuft, aber es fehlt noch etwas —"
  info "ohne AI-Schlüssel ist das normal und wird mit Schritt 2 unten grün."
fi
info "Adresse (lokal):   http://${BIND}:${PORT}"
info "Benutzer:          $(sed -n 's/^JARVIS_CC_ADMIN_USER=\([^#]*\).*/\1/p' "$ENV_FILE" | tr -d '[:space:]' | head -1)"
# Auch beim zweiten Lauf das echte Passwort zeigen: es steht ohnehin in einer
# Datei, die nur root lesen darf, und Raten hilft niemandem.
ENV_PW="$(sed -n 's/^JARVIS_CC_ADMIN_PASSWORD=\(.*\)$/\1/p' "$ENV_FILE" | head -1)"
if [ -n "$GENERATED_PW" ]; then
  info "Passwort:          ${GENERATED_PW}"
  warn "Dieses Passwort steht nur hier und in command_center/.env — jetzt notieren."
elif [ -n "$ENV_PW" ]; then
  info "Passwort:          ${ENV_PW}"
  info "                   (aus command_center/.env, unverändert übernommen)"
else
  info "Passwort:          steht in command_center/.env unter JARVIS_CC_ADMIN_PASSWORD"
fi

bold ""
bold "  Nächste Schritte"
info "1. Von außen erreichbar machen: Reverse Proxy auf ${BIND}:${PORT}, z. B. mit Caddy:"
info "     jarvis.deine-domain.de {"
info "         reverse_proxy ${BIND}:${PORT}"
info "     }"
info "2. AI-Schlüssel eintragen in command_center/.env (ANTHROPIC_API_KEY, OPENAI_API_KEY,"
info "   GEMINI_API_KEY oder LOCAL_LLM_URL), dann:"
info "     $COMPOSE -f docker-compose.command-center.yml up -d"
info "3. Im Dashboard unter Einstellungen ein Maschinen-Token anlegen und auf dem PC"
info "   als JARVIS_GATEWAY_TOKEN setzen — dann steuert der Server den Desktop mit."
bold ""
info "Logs:    $COMPOSE -f docker-compose.command-center.yml logs -f"
info "Stoppen: $COMPOSE -f docker-compose.command-center.yml down"
bold ""
