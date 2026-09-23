#!/usr/bin/env bash
# Reyes Office ausliefern: Design aus dem Dashboard neu erzeugen und nach /var/www/reyes-app kopieren (Caddy liest dort).
set -euo pipefail
R=/root/jarvis/reyes-app; F=/root/jarvis/command_center/frontend/src; OUT=/var/www/reyes-app
mkdir -p "$OUT/icons"
{ echo "/* Reyes Office — Design 1:1 aus dem Jarvis-Dashboard (tokens, base, modern, polish, shell, braincore). Nicht von Hand ändern: deploy.sh erzeugt es neu. */"
  cat $F/design/tokens.css; sed '/^@import/d' $F/design/base.css; cat $F/design/modern.css $F/design/polish.css $F/app/shell/shell.css $F/modules/home/braincore.css; } > "$R/dashboard.css"
cp -a "$R"/*.mjs "$R"/dashboard.css "$R"/app.css "$R"/sw.js "$R"/manifest.webmanifest "$R"/index.html "$OUT/"   # alle Module (*.mjs), damit keine neue Datei vergessen wird
cp -a "$R"/icons/. "$OUT/icons/"
chmod -R a+rX "$OUT"
echo "Reyes Office ausgeliefert nach $OUT"
