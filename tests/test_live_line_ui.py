"""
Die offene Leitung im Browser: überlebt sie einen Seitenwechsel?

Startet den echten Server auf einem freien Port und lässt tests/live_line_ui.mjs
in Chromium dagegen laufen. Kein Netz nach außen, kein Schlüssel, kein Mikrofon —
beides ist in der Seite ersetzt.

Ohne node oder Playwright im System wird der Test übersprungen und sagt das
auch; er behauptet nicht, bestanden zu haben.

Run:  python tests/test_live_line_ui.py
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "command_center" / "backend" / "static" / "index.html"
SCRIPT = Path(__file__).with_name("live_line_ui.mjs")


def find_playwright() -> str:
    """Playwright liegt je nach Rechner woanders — erst suchen, dann urteilen."""
    candidates = [
        ROOT / "command_center" / "frontend" / "node_modules" / "playwright" / "index.mjs",
        Path("/opt/node22/lib/node_modules/playwright/index.mjs"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    try:
        out = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20)
        p = Path(out.stdout.strip()) / "playwright" / "index.mjs"
        if out.returncode == 0 and p.exists():
            return str(p)
    except Exception:  # noqa: BLE001
        pass
    return ""


def skip(reason: str) -> int:
    print(f"\n  ÜBERSPRUNGEN: {reason}")
    print("  (Der Test lief nicht — das ist kein bestandener Test.)\n")
    return 0


def main() -> int:
    print("\n=== Live-Leitung im Browser ===\n")
    if not shutil.which("node"):
        return skip("node ist nicht installiert")
    pw = find_playwright()
    if not pw:
        return skip("Playwright ist nicht installiert (npm i -g playwright)")
    if not STATIC.exists():
        return skip("das Dashboard ist nicht gebaut (cd command_center/frontend && npx vite build)")

    tmp = tempfile.mkdtemp(prefix="jarvis-live-ui-")
    os.environ.update({
        "JARVIS_CC_DATA_DIR": tmp, "JARVIS_CC_ADMIN_USER": "admin",
        "JARVIS_CC_ADMIN_PASSWORD": "adminpass123", "JARVIS_CC_SECURE_COOKIES": "false",
        "JARVIS_MASTER_AGENT_MODE": "auto",
    })
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LOCAL_LLM_URL",
              "JARVIS_GATEWAY_TOKEN", "N8N_BASE_URL"):
        os.environ.pop(k, None)
    # Nur damit die Oberfläche die Leitung als verfügbar meldet. Der WebSocket
    # ist in der Seite ersetzt, es geht kein Byte nach außen.
    os.environ["OPENAI_API_KEY"] = "sk-test-nur-fuer-die-oberflaeche"

    import uvicorn  # noqa: PLC0415

    from command_center.backend.app import create_app  # noqa: PLC0415
    from command_center.backend.config import reset_settings  # noqa: PLC0415

    reset_settings()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(120):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        print("  FAIL Testserver startet nicht")
        return 1

    env = {**os.environ, "JARVIS_TEST_BASE": f"http://127.0.0.1:{port}",
           "JARVIS_TEST_PLAYWRIGHT": pw}
    run = subprocess.run(["node", str(SCRIPT)], env=env, cwd=str(ROOT), timeout=300)
    server.should_exit = True
    return run.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
