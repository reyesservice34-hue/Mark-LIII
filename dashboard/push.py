"""
dashboard/push.py — Web Push (VAPID) helper for the JARVIS Remote dashboard.

Lets JARVIS ring or notify a paired phone even when the dashboard page isn't
open on it, as long as the phone has a network connection and previously
subscribed via the browser's Push API. The push itself travels through
Apple's / Google's push service, not the LAN dashboard — so it needs the
computer running JARVIS to have outbound internet access (the phone does
NOT need to be on the same network once it has subscribed).

A VAPID keypair identifies JARVIS to the push services; it is generated once
and cached in config/vapid_private_key.pem — never regenerate it, or every
phone that already subscribed would need to re-subscribe.

Install:  pip install pywebpush
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

BASE_DIR        = Path(__file__).resolve().parent.parent
VAPID_KEY_FILE  = BASE_DIR / "config" / "vapid_private_key.pem"
VAPID_CLAIM_SUB = "mailto:jarvis-assistant@localhost"

_PUSH_OK = False
try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    from py_vapid.utils import b64urlencode
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _PUSH_OK = True
except ImportError:
    pass

_vapid_cache: Optional["Vapid"] = None


def push_available() -> bool:
    return _PUSH_OK


def _vapid() -> "Vapid":
    global _vapid_cache
    if _vapid_cache is None:
        VAPID_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Vapid.from_file() generates + saves a fresh key the first time the
        # file doesn't exist yet, and just loads it on every call after that.
        _vapid_cache = Vapid.from_file(str(VAPID_KEY_FILE))
    return _vapid_cache


def get_public_key_b64() -> str:
    """Base64url (no padding) uncompressed EC point — the browser's
    PushManager.subscribe({applicationServerKey}) wants exactly this."""
    if not _PUSH_OK:
        return ""
    raw = _vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


def send_web_push(subscription: dict, payload: dict, ttl: int = 60) -> tuple[bool, bool]:
    """Send one push message.

    Returns (sent_ok, subscription_dead). ``subscription_dead`` is True when
    the push service reports the subscription no longer exists (410/404) —
    the caller should drop it so it stops retrying a phone that unsubscribed
    or reinstalled the PWA.
    """
    if not _PUSH_OK or not subscription:
        return False, False
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=_vapid(),
            vapid_claims={"sub": VAPID_CLAIM_SUB},
            ttl=ttl,
        )
        return True, False
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        return False, status in (404, 410)
    except Exception as e:
        print(f"[Push] ⚠️ {e}")
        return False, False
