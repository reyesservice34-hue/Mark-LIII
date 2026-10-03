"""WhatsApp (Twilio) — eine Telefonnummer schreibt mit der echten MIA.

Zwei Zugriffsgrenzen, nicht eine: die Twilio-Signatur beweist nur "das ist
wirklich Twilio", nicht "das ist eine berechtigte Person". Die eigentliche
Erlaubnis ist die `whatsapp_links`-Tabelle (modules/whatsapp verwaltet sie) —
eine Nummer ohne Eintrag dort bekommt nie einen echten Agentenlauf, egal wie
gültig die Signatur ist.

Zugangsdaten bleiben in der .env auf dem Server, wie bei services/phone.py.
TWILIO_ACCOUNT_SID und TWILIO_AUTH_TOKEN sind dieselben wie für die
Anrufe (phone.py) — ein Twilio-Konto, zwei Kanäle. JARVIS_CC_WHATSAPP_URL ist
die öffentliche Adresse, unter der Twilio den Webhook tatsächlich aufruft;
sie ist Teil dessen, was Twilio signiert, und muss deshalb exakt stimmen
(nicht aus der Anfrage selbst abgeleitet — hinter einem Reverse Proxy wäre
das unzuverlässig oder manipulierbar).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

import httpx

API = "https://api.twilio.com/2010-04-01"

# Läuft in einem Prozess (kein Multi-Worker-Deployment hier, siehe db.py-Kommentar
# zu SQLite als Single-Writer) — ein In-Memory-Set reicht, um doppelte Läufe für
# dieselbe Nummer zu verhindern, wenn jemand schnell zweimal hintereinander schreibt.
_inflight: set[str] = set()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def missing() -> list[str]:
    return [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER",
                        "JARVIS_CC_WHATSAPP_URL") if not _env(k)]


def configured() -> bool:
    return not missing()


def status(db) -> dict:
    links = db.fetchone("SELECT COUNT(*) AS n FROM whatsapp_links WHERE disabled=0")
    return {"konfiguriert": configured(), "fehlt": missing(),
            "webhook_url": _env("JARVIS_CC_WHATSAPP_URL"),
            "verknuepfte_nummern": (links or {}).get("n", 0)}


def normalize_phone(raw: str) -> str:
    """'whatsapp:+49 176 64096121' -> '+4917664096121'. Kein Format-Raten darüber hinaus —
    Twilio liefert From bereits in E.164, wir entfernen nur das Präfix und Leerraum."""
    raw = (raw or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw[9:]
    return re.sub(r"[^\d+]", "", raw)


# ── Twilio-Signatur ──────────────────────────────────────────────────────────

def verify_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Twilios dokumentierter Algorithmus: URL + sortierte key+value-Paare (unescaped),
    HMAC-SHA1 mit dem Auth Token, base64. Siehe
    https://www.twilio.com/docs/usage/security#validating-requests

    `url` MUSS die Adresse sein, die Twilio wirklich aufgerufen hat — nicht aus
    request.url abgeleitet (hinter Caddy/uvicorn kann das intern abweichen oder,
    bei falsch konfigurierten Trust-Proxy-Headern, sogar fälschbar sein).
    """
    if not auth_token or not signature:
        return False
    base = url
    for key in sorted(params.keys()):
        base += key + params[key]
    digest = hmac.new(auth_token.encode("utf-8"), base.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ── ausgehend ─────────────────────────────────────────────────────────────────

async def send_whatsapp(to: str, text: str) -> dict:
    """Schickt `text` an `to` (E.164, ohne 'whatsapp:'-Präfix). Wirft nie,
    gibt {"gesendet": bool, "hinweis": str} zurück. Loggt nie den Auth Token
    oder die volle Twilio-Antwort (die den Nachrichtentext spiegelt)."""
    if not configured():
        return {"gesendet": False, "hinweis": "WhatsApp nicht eingerichtet, es fehlt: " + ", ".join(missing())}
    sid, tok = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
    from_number = _env("TWILIO_WHATSAPP_NUMBER")
    body = text.strip()[:1500] or "(leere Antwort)"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{API}/Accounts/{sid}/Messages.json", auth=(sid, tok),
                             data={"From": from_number, "To": f"whatsapp:{to}", "Body": body})
    except Exception as e:  # noqa: BLE001
        return {"gesendet": False, "hinweis": f"Twilio nicht erreichbar: {type(e).__name__}"}
    if r.status_code >= 300:
        return {"gesendet": False, "hinweis": f"Twilio hat die Nachricht abgelehnt (HTTP {r.status_code})"}
    return {"gesendet": True, "hinweis": "Nachricht gesendet."}


# ── Verknüpfung (Telefonnummer <-> MIA-Nutzer) ────────────────────────────────

def get_link(db, phone: str) -> dict | None:
    return db.fetchone("SELECT * FROM whatsapp_links WHERE phone_number=?", (phone,))


def list_links(db) -> list[dict]:
    return db.fetchall("SELECT * FROM whatsapp_links ORDER BY created_at DESC")


def upsert_link(db, phone: str, user_id: str, created_by: str) -> dict:
    from ..db import now_iso
    existing = get_link(db, phone)
    if existing:
        db.execute("UPDATE whatsapp_links SET user_id=?, disabled=0 WHERE phone_number=?", (user_id, phone))
    else:
        db.insert("whatsapp_links", {"phone_number": phone, "user_id": user_id, "conversation_id": None,
                                     "disabled": 0, "created_at": now_iso(), "created_by": created_by})
    return get_link(db, phone)


def set_link_conversation(db, phone: str, conversation_id: str) -> None:
    db.execute("UPDATE whatsapp_links SET conversation_id=? WHERE phone_number=?", (conversation_id, phone))


def delete_link(db, phone: str) -> bool:
    cur = db.execute("DELETE FROM whatsapp_links WHERE phone_number=?", (phone,))
    return bool(cur.rowcount)


# ── gleichzeitige Läufe je Nummer verhindern ──────────────────────────────────

def start_inflight(phone: str) -> bool:
    """True = reserviert, los geht's. False = läuft bereits ein Lauf für diese Nummer."""
    if phone in _inflight:
        return False
    _inflight.add(phone)
    return True


def end_inflight(phone: str) -> None:
    _inflight.discard(phone)
