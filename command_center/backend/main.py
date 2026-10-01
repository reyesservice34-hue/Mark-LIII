"""Entry point: `python -m command_center.backend.main` or uvicorn `command_center.backend.main:app`."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make the repository root importable (core/, memory/) when run from anywhere.
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from command_center.backend.app import create_app  # noqa: E402
from command_center.backend.config import get_settings  # noqa: E402

logging.basicConfig(level=os.environ.get("JARVIS_CC_LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()


def run() -> None:
    import uvicorn
    s = get_settings()
    uvicorn.run("command_center.backend.main:app", host=s.host, port=s.port, proxy_headers=s.trust_proxy,
                forwarded_allow_ips="*" if s.trust_proxy else None, log_level="info")


if __name__ == "__main__":
    run()
