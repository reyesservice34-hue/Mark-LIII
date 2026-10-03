"""Anruf aufs Handy bei dringenden Fällen (Twilio Programmable Voice).

Jarvis ruft die eigene Nummer des Nutzers an und liest die Meldung vor. Ein Konto bei Twilio, eine Absendernummer und die Nummer des
Nutzers sind nötig; solange eines fehlt, ruft nichts an und `status()` sagt genau, was fehlt. Zugangsdaten bleiben in der .env auf dem Server.

Schutz vor Dauerklingeln: höchstens ein Anruf je Abkühlzeit (JARVIS_CC_CALL_COOLDOWN_MIN, Standard 10 Minuten), auch bei mehreren Meldungen
hintereinander. Ruft nur die hinterlegte Nummer an, nie eine, die ein Agent oder eine Nachricht nennt.
"""
from __future__ import annotations

import os
import time
from xml.sax.saxutils import escape

import httpx

_last_call = 0.0
API = "https://api.twilio.com/2010-04-01"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def missing() -> list[str]:
    return [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "JARVIS_CC_CALL_TO") if not _env(k)]


def configured() -> bool:
    return not missing()


def auto_min() -> str:
    """Ab welcher Schwere Meldungen von selbst anrufen: critical (Standard) oder off."""
    v = _env("JARVIS_CC_CALL_MIN", "critical").lower()
    return v if v in ("critical", "off") else "critical"


def status() -> dict:
    to = _env("JARVIS_CC_CALL_TO")
    return {"konfiguriert": configured(), "fehlt": missing(), "nummer": (to[:3] + "…" + to[-3:]) if len(to) > 8 else "",
            "auto": auto_min() != "off" and configured(), "regel": "nur bei kritischen Fällen" if auto_min() != "off" else "aus"}


def twiml(text: str) -> str:
    voice = _env("JARVIS_CC_CALL_VOICE", "Polly.Hans")
    body = escape(" ".join(str(text).split())[:600])
    say = f'<Say language="de-DE" voice="{escape(voice)}">{body}</Say>'
    return f'<Response><Pause length="1"/>{say}<Pause length="2"/>{say}</Response>'


async def _post(url: str, auth: tuple[str, str], data: dict) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, auth=auth, data=data)
    return r.status_code, r.text


async def call(text: str, *, force: bool = False) -> dict:
    """Ruft an und liest `text` zweimal vor. Gibt {"angerufen": bool, "hinweis": str} zurück und wirft nie."""
    global _last_call
    if not configured():
        return {"angerufen": False, "hinweis": "Anruf nicht eingerichtet, es fehlt: " + ", ".join(missing())}
    cooldown = float(_env("JARVIS_CC_CALL_COOLDOWN_MIN", "10") or 10) * 60
    if not force and time.monotonic() - _last_call < cooldown and _last_call:
        return {"angerufen": False, "hinweis": "Es wurde gerade erst angerufen, kein zweiter Anruf innerhalb der Abkühlzeit."}
    sid, tok = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
    try:
        code, body = await _post(f"{API}/Accounts/{sid}/Calls.json", (sid, tok),
                                 {"To": _env("JARVIS_CC_CALL_TO"), "From": _env("TWILIO_FROM"), "Twiml": twiml(text)})
    except Exception as e:  # noqa: BLE001
        return {"angerufen": False, "hinweis": f"Twilio nicht erreichbar: {type(e).__name__}"}
    if code >= 300:
        return {"angerufen": False, "hinweis": f"Twilio hat den Anruf abgelehnt (HTTP {code})."}
    _last_call = time.monotonic()
    return {"angerufen": True, "hinweis": "Anruf ausgelöst."}


# ── Anrufe bei Dritten (Kunden) ───────────────────────────────────────────────
# Bewusst getrennt von call(): call() ruft NUR die eine hinterlegte Nummer (Sicherheitsbremse,
# siehe Moduldoc oben) — call_contact() darf jede Nummer anrufen, die ihr übergeben wird, weil sie
# nur über das freigabepflichtige Werkzeug notify.call_kunde erreichbar ist (risk="high",
# requires_approval=True in orchestrator/builtin_tools.py). Eigene Abkühlzeit je Zielnummer, damit
# ein Kunde unabhängig vom eigenen Notfall-Anruf nicht mehrfach hintereinander angerufen wird.
_last_contact_call: dict[str, float] = {}


def missing_contact() -> list[str]:
    return [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM") if not _env(k)]


def configured_contact() -> bool:
    return not missing_contact()


async def call_contact(to: str, text: str, *, force: bool = False) -> dict:
    """Ruft `to` (E.164, z.B. +4917612345678) an und liest `text` vor. Wirft nie,
    gibt {"angerufen": bool, "hinweis": str} zurück."""
    if not configured_contact():
        return {"angerufen": False, "hinweis": "Anruf nicht eingerichtet, es fehlt: " + ", ".join(missing_contact())}
    to = to.strip()
    if not to.startswith("+") or not to[1:].replace(" ", "").isdigit():
        return {"angerufen": False, "hinweis": "Nummer muss im internationalen Format stehen (z.B. +4917612345678)."}
    cooldown = float(_env("JARVIS_CC_CALL_COOLDOWN_MIN", "10") or 10) * 60
    last = _last_contact_call.get(to, 0.0)
    if not force and time.monotonic() - last < cooldown and last:
        return {"angerufen": False, "hinweis": "Diese Nummer wurde gerade erst angerufen, kein zweiter Anruf innerhalb der Abkühlzeit."}
    sid, tok = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
    try:
        code, body = await _post(f"{API}/Accounts/{sid}/Calls.json", (sid, tok),
                                 {"To": to, "From": _env("TWILIO_FROM"), "Twiml": twiml(text)})
    except Exception as e:  # noqa: BLE001
        return {"angerufen": False, "hinweis": f"Twilio nicht erreichbar: {type(e).__name__}"}
    if code >= 300:
        return {"angerufen": False, "hinweis": f"Twilio hat den Anruf abgelehnt (HTTP {code})."}
    _last_contact_call[to] = time.monotonic()
    return {"angerufen": True, "hinweis": "Anruf ausgelöst."}
