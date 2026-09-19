"""
desktop_bridge — one typed or transcribed utterance in, one JARVIS answer out.

This is the seam between the desktop and the control plane. Its whole job is to
keep the desktop from having a personality of its own: it does not compose,
rephrase, or add a persona. It sends what the user said to the server, waits for
the server to do the work, and hands back EXACTLY the server's answer to be
spoken. The "Sir" and the invented tool results came from a local brain; there
is no local brain here to produce them.

What it owns:
  * a stable actor (the desktop) and a persistent conversation_id, so desktop and
    WhatsApp land on the same server-side identity and memory;
  * waiting through a whole multi-step order — the reply is only final when the
    server says completed, failed, or awaiting_approval;
  * turning the three server outcomes into three plain things to say: the answer,
    a concrete approval request, or a clearly-labelled problem (offline / token /
    timeout) — never a pretend success.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.control_plane import (
    CommandResult,
    ControlPlane,
    ControlPlaneAuthError,
    ControlPlaneError,
    ControlPlaneOffline,
)


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
STATE_PATH  = BASE_DIR / "memory" / "conversation_state.json"


@dataclass
class DesktopReply:
    """What the desktop should SAY, and what kind of outcome it was. `speak` is
    read aloud verbatim — for a normal answer it is the server's own words, never
    anything this process wrote."""
    speak: str
    kind: str                    # answer | approval | offline | auth | timeout | error | not_configured
    approval_code: str = ""
    conversation_id: str = ""

    def spoken(self) -> bool:
        return bool(self.speak.strip())


class DesktopBridge:
    def __init__(self, client: Optional[ControlPlane] = None,
                 state_path: Path = STATE_PATH):
        self.client = client or ControlPlane()
        self.state_path = Path(state_path)
        self._conversation_id = self._load_conversation()

    # -- conversation continuity across turns and restarts --
    def _load_conversation(self) -> str:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return str(data.get("conversation_id", "") or "")
        except Exception:
            return ""

    def _save_conversation(self, conversation_id: str) -> None:
        if not conversation_id or conversation_id == self._conversation_id:
            return
        self._conversation_id = conversation_id
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"conversation_id": conversation_id}, indent=2),
                encoding="utf-8")
        except Exception:
            pass   # continuity is a convenience; never let it break a reply

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    def reset_conversation(self) -> None:
        self._conversation_id = ""
        try:
            self.state_path.unlink(missing_ok=True)
        except Exception:
            pass

    # -- the one entry point --
    def handle(self, text: str,
               on_status: Optional[Callable[[CommandResult], None]] = None) -> DesktopReply:
        command = (text or "").strip()
        if not command:
            return DesktopReply(speak="", kind="error")

        if not self.client.configured():
            return DesktopReply(
                speak="Ich bin gerade nicht mit der Leitstelle verbunden — es fehlt der "
                      "Zugangs-Token. Trag ihn in den Einstellungen ein, dann bin ich sofort da.",
                kind="not_configured")

        try:
            res = self.client.run(command, conversation_id=self._conversation_id or None,
                                  on_status=on_status)
        except ControlPlaneAuthError:
            return DesktopReply(
                speak="Die Leitstelle hat meinen Zugang abgelehnt. Der Token stimmt nicht mehr — "
                      "bitte einmal neu hinterlegen.",
                kind="auth")
        except ControlPlaneOffline:
            return DesktopReply(
                speak="Ich erreiche die Leitstelle im Moment nicht. Ich habe nichts ausgeführt — "
                      "sobald die Verbindung wieder steht, kümmere ich mich darum.",
                kind="offline")
        except ControlPlaneError as e:
            return DesktopReply(
                speak=f"Da ist bei der Leitstelle etwas schiefgegangen: {e}. Ausgeführt habe ich nichts.",
                kind="error")

        self._save_conversation(res.conversation_id)

        # 1. The server wants a human to approve something irreversible/external.
        if res.awaiting_approval():
            spoken = res.result.strip() or ("Dafür brauche ich noch deine ausdrückliche Freigabe, "
                                            "bevor ich es mache.")
            if res.approval_code:
                spoken += f" Freigabe-Code: {res.approval_code}."
            return DesktopReply(speak=spoken, kind="approval",
                                approval_code=res.approval_code,
                                conversation_id=res.conversation_id)

        # 2. Done — read back EXACTLY what the server said.
        if res.ok():
            spoken = res.result.strip() or "Erledigt."
            return DesktopReply(speak=spoken, kind="answer",
                                conversation_id=res.conversation_id)

        # 3. The server tried and failed — say so plainly, do not invent a success.
        problem = res.error.strip() or "Das hat auf der Leitstelle nicht geklappt."
        return DesktopReply(speak=problem, kind="error", conversation_id=res.conversation_id)
