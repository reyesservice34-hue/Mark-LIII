#!/usr/bin/env bash
# scripts/setup_headless_server.sh
#
# Installs and runs Mark-LIII on a headless Linux server (no monitor, no
# physical mic/speakers, accessed only via SSH) — e.g. a bare Ubuntu VPS.
#
# Empirically derived: main.py imports PyQt6 (ui.py) and sounddevice, both of
# which need native libraries a minimal server image doesn't ship. Verified
# on a clean Ubuntu 24.04 container: without these packages, `import
# sounddevice` raises "PortAudio library not found" and PyQt6's QApplication
# raises "libEGL.so.1: cannot open shared object file". With them plus
# QT_QPA_PLATFORM=offscreen, main.py starts, the action loader reports
# "[Audio] 0 input / 0 output devices found" as an informational line (not a
# crash — the phone's mic is relayed into the Gemini Live session instead of
# a local one), and the dashboard serves HTTPS correctly.
#
# What this script does NOT do: get you a Gemini API key (see step 3 below,
# free tier at https://aistudio.google.com/apikey), or make browser_control
# work (needs `playwright install chromium` separately — skip it if you
# don't use that feature; the action loader disables it gracefully without
# it, everything else still runs).
#
# Usage:  sudo bash scripts/setup_headless_server.sh <server-hostname-or-ip>

set -euo pipefail

HOSTNAME_OR_IP="${1:-}"
if [ -z "$HOSTNAME_OR_IP" ]; then
  echo "Usage: $0 <server-hostname-or-ip>   (e.g. jarvis.jarvis-reyes.de)"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "== 1/5  System libraries (PortAudio + headless Qt) =="
apt-get update -qq
apt-get install -y --quiet \
  python-is-python3 python3-pip python3-venv \
  libportaudio2 \
  libegl1 libgl1 libxkbcommon0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
  libxcb-xinerama0 libdbus-1-3 libxcb1 libx11-xcb1

echo "== 2/5  Python dependencies =="
# --ignore-installed avoids fighting distro-packaged versions of the same libs.
pip install --ignore-installed -r requirements.txt || {
  echo "!! Some optional packages failed to build (commonly pygetwindow/pyautogui" \
       "on a headless box with no desktop to control). This is expected here and" \
       "does not stop JARVIS from starting — continuing."
}

echo "== 3/5  Gemini API key =="
if [ ! -s config/api_keys.json ] || ! grep -q gemini_api_key config/api_keys.json 2>/dev/null; then
  echo "No config/api_keys.json with a gemini_api_key yet."
  echo "Get a free key at https://aistudio.google.com/apikey, then run:"
  echo
  echo "  mkdir -p config && cat > config/api_keys.json <<JSON"
  echo '  {"gemini_api_key": "YOUR_KEY_HERE", "os_system": "linux"}'
  echo "  JSON"
  echo
  echo "...and re-run this script (steps 1-2 are safe to skip once done)."
  exit 1
fi

echo "== 4/5  TLS certificate for the dashboard (required for iPhone Safari) =="
if [ ! -f config/certs/jarvis.key ]; then
  python3 dashboard/generate_cert.py "$HOSTNAME_OR_IP"
else
  echo "config/certs/jarvis.key already exists — leaving it as is."
fi

echo "== 5/5  Done. Start JARVIS with: =="
echo "  QT_QPA_PLATFORM=offscreen python3 main.py"
echo
echo "To keep it running after you disconnect SSH, use the systemd service"
echo "in scripts/mark-liii.service (see its header comment for install steps)."
