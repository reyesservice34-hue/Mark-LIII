#!/bin/sh
# Startet die Hermes-Werkzeugbrücke im Hintergrund und danach das Command Center
# (PID 1 bleibt uvicorn, damit Docker-Signale und Healthcheck wie bisher wirken).
set -e
if [ "${MIA_HERMES_TOOLS:-true}" = "true" ]; then
    python /app/command_center/docker/hermes_tools_bridge.py >/data/hermes-bridge.log 2>&1 &
fi
exec "$@"
