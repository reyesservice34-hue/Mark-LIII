"""
whatsapp — JARVIS writes to you, as a voice note, over WhatsApp Web.

Why WhatsApp *Web* and not the desktop app: the existing send_message action
drives the desktop window with pyautogui, which can type text but cannot
reliably attach a file — and a voice note is a file. WhatsApp Web can, Playwright
is already a dependency of this project, and .gitignore already reserves
``config/whatsapp_web/`` for a linked session. It costs nothing: you scan a QR
code once, the browser profile is kept, and every later send reuses it.

The voice comes from core/speech_out.py, which asks for the same voice the live
session speaks with — so the note in your pocket sounds like the assistant in
the room.

**What this file cannot promise.** WhatsApp Web is someone else's web app; its
markup changes without notice and cannot be tested from a machine with no
WhatsApp account. Every selector is therefore a named constant below, each step
checks that it found what it expected, and a failure says which step failed and
what to look at — instead of a silent success that never delivered anything.
Treat a first real send as a test, with `dry_run` first.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from memory.config_manager import get_plugin_setting
from core.speech_out import SpeechError, describe_voice, synthesize

NAMESPACE = "whatsapp"


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR     = get_base_dir()
PROFILE_DIR  = BASE_DIR / "config" / "whatsapp_web"     # already gitignored
WEB_URL      = "https://web.whatsapp.com/"

# ── Selectors, in one place so a WhatsApp redesign is a five-minute fix ──────
SEL_CHAT_LIST   = '#pane-side'                       # only present once logged in
SEL_QR          = '[data-testid="qrcode"], canvas[aria-label*="scan" i]'
SEL_SEARCH      = '[contenteditable="true"][data-tab="3"]'
SEL_RESULT_ROW  = '#pane-side [role="listitem"]'
SEL_MSG_BOX     = '[contenteditable="true"][data-tab="10"], [contenteditable="true"][data-tab="6"]'
SEL_FILE_INPUT  = 'input[type="file"]'
SEL_SEND_BTN    = '[data-testid="send"], [aria-label="Senden"], [aria-label="Send"]'

LOGIN_TIMEOUT   = 180      # seconds to scan the QR
STEP_TIMEOUT    = 30_000   # ms per page step


class WhatsAppError(Exception):
    """Something the user has to know about, phrased for speaking aloud."""


def _setting(key: str, default):
    value = get_plugin_setting(NAMESPACE, key, default)
    return default if value in (None, "") else value


def _default_contact() -> str:
    return str(_setting("default_contact", "")).strip()


def _headless() -> bool:
    # Linking needs a visible window (there is a QR to scan). Sending does not,
    # but a visible window is the honest default while this is new: you can see
    # what it did. Flip it in plugin settings once you trust it.
    return bool(_setting("headless", False))


def _browser_context(headless: bool):
    """A persistent context — the whole point is that the login survives."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise WhatsAppError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        )

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
    except Exception as e:
        pw.stop()
        raise WhatsAppError(f"could not start the browser: {e}")
    return pw, context


def _is_linked(page) -> bool:
    try:
        page.wait_for_selector(SEL_CHAT_LIST, timeout=STEP_TIMEOUT)
        return True
    except Exception:
        return False


def link(_values: dict | None = None) -> tuple[bool, str]:
    """Open WhatsApp Web so the QR code can be scanned once. Called by the
    LINK WHATSAPP button in plugin settings, and by action='link'."""
    try:
        pw, context = _browser_context(headless=False)
    except WhatsAppError as e:
        return False, str(e)

    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(WEB_URL, timeout=60_000)

        if _is_linked(page):
            return True, "WhatsApp Web is already linked — nothing to scan."

        deadline = time.time() + LOGIN_TIMEOUT
        while time.time() < deadline:
            try:
                page.wait_for_selector(SEL_CHAT_LIST, timeout=5_000)
                return True, "Linked. The session is stored, so this is a one-time step."
            except Exception:
                continue
        return False, (f"No login within {LOGIN_TIMEOUT}s. Open WhatsApp on your phone → "
                       f"Linked devices → Link a device, and scan the code in the window.")
    except Exception as e:
        return False, f"Linking failed: {e}"
    finally:
        try:
            context.close()
            pw.stop()
        except Exception:
            pass


def _open_chat(page, contact: str) -> None:
    box = page.wait_for_selector(SEL_SEARCH, timeout=STEP_TIMEOUT)
    box.click()
    box.fill("")
    box.type(contact, delay=40)
    page.wait_for_timeout(1200)

    rows = page.query_selector_all(SEL_RESULT_ROW)
    if not rows:
        raise WhatsAppError(f"no chat found for '{contact}' — check the spelling as it "
                            f"appears in WhatsApp")
    rows[0].click()
    page.wait_for_selector(SEL_MSG_BOX, timeout=STEP_TIMEOUT)


def _send_text(page, text: str) -> None:
    box = page.wait_for_selector(SEL_MSG_BOX, timeout=STEP_TIMEOUT)
    box.click()
    box.type(text, delay=10)
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)


def _send_file(page, path: Path) -> None:
    inputs = page.query_selector_all(SEL_FILE_INPUT)
    if not inputs:
        raise WhatsAppError("WhatsApp Web did not offer a file input — the page layout "
                            "may have changed (see SEL_FILE_INPUT in this plugin)")
    # The audio/any input is normally the last one mounted in the chat footer.
    inputs[-1].set_input_files(str(path))
    page.wait_for_timeout(1500)
    send = page.query_selector(SEL_SEND_BTN)
    if send is None:
        raise WhatsAppError("the attachment preview had no send button — layout change "
                            "(see SEL_SEND_BTN)")
    send.click()
    page.wait_for_timeout(2000)


def _deliver(contact: str, text: str | None, audio: Path | None) -> str:
    pw, context = _browser_context(_headless())
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(WEB_URL, timeout=60_000)
        if not _is_linked(page):
            raise WhatsAppError("WhatsApp Web is not linked yet — press LINK WHATSAPP in "
                                "plugin settings and scan the QR code once.")
        _open_chat(page, contact)
        if audio is not None:
            _send_file(page, audio)
        if text:
            _send_text(page, text)
        return f"Sent to {contact}."
    finally:
        try:
            context.close()
            pw.stop()
        except Exception:
            pass


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "voice")).strip().lower() or "voice"
    message = str(p.get("message", "")).strip()
    contact = str(p.get("contact", "")).strip() or _default_contact()
    dry_run = bool(p.get("dry_run", False))

    def log(msg: str) -> None:
        print(f"[WhatsApp] {msg}")
        if player:
            try:
                player.write_log(f"JARVIS: {msg}")
            except Exception:
                pass

    try:
        if action == "link":
            ok, msg = link()
            return msg

        if action == "status":
            return (f"Contact: {contact or 'not set'} · voice: {describe_voice()} · "
                    f"profile: {'linked' if PROFILE_DIR.exists() else 'not linked yet'}")

        if not message:
            return "What should I say in the message?"
        if not contact:
            return ("I do not know who to send it to. Set a default contact in plugin "
                    "settings, or name one in the request.")

        audio = None
        if action in ("voice", "voice_note", "sprachnachricht", ""):
            log("Recording the voice note…")
            try:
                audio = synthesize(message, name=f"note_{int(time.time())}")
            except SpeechError as e:
                # Falling back to text is better than sending nothing, but the
                # user must know it happened — they asked for a voice note.
                log(f"Speech failed ({e}) — sending it as text instead.")
                audio = None
        elif action not in ("text", "message"):
            return (f"I do not know the WhatsApp action '{action}'. I can send a voice "
                    f"note, send text, link the account, or report status.")

        if dry_run:
            return (f"Dry run: would send to {contact} — "
                    f"{'voice note ' + audio.name if audio else 'text'} "
                    f"({len(message)} characters, voice: {describe_voice()}).")

        result = _deliver(contact, message if audio is None else "", audio)
        log(result)
        return result

    except WhatsAppError as e:
        return str(e)
    except Exception as e:
        return f"The WhatsApp message failed: {e}"


PLUGIN_SETTINGS = {
    "namespace": NAMESPACE,
    "title": "💬  WHATSAPP VOICE NOTES",
    "fields": [
        {"key": "default_contact", "label": "Default contact (exactly as in WhatsApp)",
         "type": "text", "placeholder": "e.g. your own name, for a note to self"},
        {"key": "headless", "label": "Send without showing the browser window",
         "type": "toggle", "default": False},
    ],
    "action": {"label": "LINK WHATSAPP (QR)", "run": link},
}


PLUGIN = {
    "name": "whatsapp",
    "description": (
        "Sends the user a WhatsApp message as a spoken voice note (or as text) — use it "
        "whenever they ask to be told something on WhatsApp, to be sent a summary, a "
        "reminder or an update by voice, or when you want to reach them while they are "
        "away from the machine. The voice is the same one you speak with. Use send_message "
        "instead for a plain text message to someone else on another platform."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "message": {
                "type": "STRING",
                "description": "What to say, in the user's language — written to be heard, not read"
            },
            "action": {
                "type": "STRING",
                "description": "voice (default) | text | link | status"
            },
            "contact": {
                "type": "STRING",
                "description": "Recipient name as it appears in WhatsApp (default: the configured contact)"
            },
            "dry_run": {
                "type": "BOOLEAN",
                "description": "Prepare everything but do not actually send"
            }
        },
        "required": ["message"]
    },
}
