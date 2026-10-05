#!/usr/bin/env bash
# Kopiert die Hermes-Laufzeit und die eigenen Hermes-Skills in den Build-Kontext
# des Command Centers (command_center/hermes_runtime/, git-ignoriert).
# Aufruf vor jedem Image-Build:  bash command_center/scripts/sync_hermes_runtime.sh
set -euo pipefail
SRC_HERMES="${HERMES_SRC:-/root/Mark-LIII/hermes/hermes-agent}"
SRC_SKILLS="${HERMES_SKILLS_SRC:-/root/Mark-LIII/hermes/skills}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/hermes_runtime"

rm -rf "$DEST"
mkdir -p "$DEST/src" "$DEST/user_skills"
# Nur das Python-Paket und die Build-Dateien; Caches, Umgebungen, Web-Frontends,
# Tests und Docs bleiben draußen (Image-Größe).
tar -C "$SRC_HERMES" \
    --exclude=.git --exclude='__pycache__' --exclude='node_modules' \
    --exclude='.venv' --exclude=venv --exclude=apps --exclude=web --exclude=website \
    --exclude=evals --exclude=docker --exclude=assets --exclude=contributors \
    --exclude=tests --exclude=tests-js --exclude=ui-tui --exclude='*.egg-info' \
    --exclude='docker-compose*' --exclude='.hermes*' \
    -cf - . | tar -C "$DEST/src" -xf -
tar -C "$SRC_SKILLS" -cf - . | tar -C "$DEST/user_skills" -xf -
echo "hermes_runtime bereit: $(du -sh "$DEST" | cut -f1)"
