"""
email — read, search and send mail, over plain IMAP and SMTP.

Why IMAP/SMTP and not a provider API: a Handwerksbetrieb's address sits wherever
it sits — IONOS, Strato, Telekom, GMX, Gmail — and every one of them speaks IMAP
and SMTP. A Gmail-shaped integration would cover one case and need a Cloud
console project for it. This needs `imaplib`, `smtplib` and `email`, all three in
the standard library: no new dependency, no account with anyone, no cent.

SENDING GOES THROUGH THE HUD GATE. A mail to a customer cannot be recalled, and
this project already decided how that is handled: core/confirm.py issues a
CONFIRM button from the *interface*, so the model cannot wave itself through by
filling in a `confirmed=yes` parameter (see that file's own account of why the
old pattern was a convention and not a gate). Reading and searching change
nothing and are not gated.

One consequence worth knowing: an agent running with no interface bound — the
`kunde` agent inside agency_agent, say — cannot send. confirm.request() refuses
when there is no HUD to ask on. That is the intended outcome: drafting is
autonomous, sending is not.

The server table below is a convenience, not a claim. It fills the host fields
when they are left blank, and the TEST CONNECTION button in plugin settings
tells you in two seconds whether the guess was right for your provider.
"""
from __future__ import annotations

import email.utils
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from core import confirm as confirm_gate
from memory.config_manager import get_plugin_setting

NAMESPACE = "email"
MAX_LISTED = 10
MAX_BODY_CHARS = 4000
ATTACH_LIMIT_MB = 15


# ── Known providers: (imap_host, imap_port, smtp_host, smtp_port, smtp_ssl) ──
PROVIDERS = {
    "gmail.com":      ("imap.gmail.com", 993, "smtp.gmail.com", 465, True),
    "googlemail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, True),
    "gmx.de":         ("imap.gmx.net", 993, "mail.gmx.net", 465, True),
    "gmx.net":        ("imap.gmx.net", 993, "mail.gmx.net", 465, True),
    "gmx.at":         ("imap.gmx.net", 993, "mail.gmx.net", 465, True),
    "gmx.ch":         ("imap.gmx.net", 993, "mail.gmx.net", 465, True),
    "web.de":         ("imap.web.de", 993, "smtp.web.de", 587, False),
    "t-online.de":    ("secureimap.t-online.de", 993, "securesmtp.t-online.de", 465, True),
    "outlook.com":    ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "outlook.de":     ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "hotmail.com":    ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "hotmail.de":     ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "live.de":        ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "ionos.de":       ("imap.ionos.de", 993, "smtp.ionos.de", 465, True),
    "1und1.de":       ("imap.ionos.de", 993, "smtp.ionos.de", 465, True),
    "strato.de":      ("imap.strato.de", 993, "smtp.strato.de", 465, True),
    "posteo.de":      ("posteo.de", 993, "posteo.de", 465, True),
    "mailbox.org":    ("imap.mailbox.org", 993, "smtp.mailbox.org", 465, True),
    "yahoo.com":      ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, True),
    "yahoo.de":       ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, True),
}


class MailError(Exception):
    """Something the user needs to hear, phrased for speaking aloud."""


@dataclass
class Account:
    address: str
    password: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    smtp_ssl: bool
    sender_name: str = ""

    def sender(self) -> str:
        return (email.utils.formataddr((self.sender_name, self.address))
                if self.sender_name else self.address)


def _setting(key: str, default=""):
    value = get_plugin_setting(NAMESPACE, key, default)
    return default if value in (None, "") else value


def _guess(domain: str):
    """A provider's servers, or the widespread imap./smtp. convention as a last
    resort — wrong often enough that TEST CONNECTION exists, never silently."""
    if domain in PROVIDERS:
        return PROVIDERS[domain]
    return (f"imap.{domain}", 993, f"smtp.{domain}", 465, True)


def account(values: Optional[dict] = None) -> Account:
    values = values or {}

    def pick(key, default=""):
        raw = values.get(key)
        return str(raw).strip() if raw not in (None, "") else str(_setting(key, default)).strip()

    address = pick("address")
    password = pick("password")
    if not address or "@" not in address:
        raise MailError("No mail address is configured. Fill it in under plugin settings.")
    if not password:
        raise MailError("No mail password is configured. Most providers need an app "
                        "password rather than the one you type into the website.")

    domain = address.rsplit("@", 1)[1].lower()
    g_ih, g_ip, g_sh, g_sp, g_ssl = _guess(domain)

    def port(key, fallback):
        try:
            return int(pick(key) or fallback)
        except (TypeError, ValueError):
            return fallback

    smtp_port = port("smtp_port", g_sp)
    return Account(
        address=address,
        password=password,
        imap_host=pick("imap_host") or g_ih,
        imap_port=port("imap_port", g_ip),
        smtp_host=pick("smtp_host") or g_sh,
        smtp_port=smtp_port,
        # 465 is implicit TLS, 587 is STARTTLS — the port decides, not a toggle
        # the user has to understand.
        smtp_ssl=(smtp_port == 465) if pick("smtp_port") else g_ssl,
        sender_name=pick("sender_name"),
    )


# ── IMAP side (reading changes nothing, so it is never gated) ────────────────

def _imap(acc: Account) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(acc.imap_host, acc.imap_port,
                                 ssl_context=ssl.create_default_context())
        conn.login(acc.address, acc.password)
        return conn
    except imaplib.IMAP4.error as e:
        raise MailError(f"The mail server refused the login ({e}). With most providers "
                        f"this means an app password is required.")
    except Exception as e:
        raise MailError(f"Could not reach the mail server {acc.imap_host}: {e}")


def _decode(raw) -> str:
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _body_of(msg) -> str:
    """Plain text if the mail has any; otherwise HTML with the tags taken out —
    a spoken assistant reading raw markup to you is worse than no body."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                    part.get("Content-Disposition", "")):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    continue
        return ""

    # Single-part mail. Plenty of business senders ship HTML with no plain
    # alternative at all, and reading markup aloud is worse than reading
    # nothing — so the same stripping applies here, not just in the branch
    # above.
    try:
        text = msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return str(msg.get_payload())
    if msg.get_content_type() == "text/html":
        return re.sub(r"<[^>]+>", " ", text)
    return text


def _fetch(acc: Account, criterion: str, limit: int, with_body: bool = False) -> list[dict]:
    import email as email_mod

    conn = _imap(acc)
    try:
        conn.select("INBOX")
        typ, data = conn.search(None, criterion)
        if typ != "OK":
            raise MailError("The mail server did not accept that search.")
        ids = (data[0] or b"").split()
        out = []
        for num in reversed(ids[-max(1, limit):]):
            typ, raw = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email_mod.message_from_bytes(raw[0][1])
            item = {
                "from": _decode(msg.get("From")),
                "subject": _decode(msg.get("Subject")) or "(no subject)",
                "date": _decode(msg.get("Date")),
            }
            if with_body:
                body = re.sub(r"[ \t]*\n[ \t]*", "\n", _body_of(msg)).strip()
                item["body"] = body[:MAX_BODY_CHARS]
            out.append(item)
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ── SMTP side (irreversible, therefore gated) ───────────────────────────────

def _build(acc: Account, to: str, subject: str, body: str,
           attachments: list[str] | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = acc.sender()
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    msg.set_content(body)

    for raw_path in (attachments or []):
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            raise MailError(f"The attachment {path.name} does not exist.")
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > ATTACH_LIMIT_MB:
            raise MailError(f"{path.name} is {size_mb:.1f} MB — most mail servers refuse "
                            f"anything over {ATTACH_LIMIT_MB} MB.")
        msg.add_attachment(path.read_bytes(), maintype="application",
                           subtype="octet-stream", filename=path.name)
    return msg


def _transmit(acc: Account, msg: EmailMessage) -> str:
    context = ssl.create_default_context()
    try:
        if acc.smtp_ssl:
            with smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=context, timeout=60) as s:
                s.login(acc.address, acc.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=60) as s:
                s.starttls(context=context)
                s.login(acc.address, acc.password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("The mail server rejected the password. Most providers need an "
                        "app password for programs like this one.")
    except Exception as e:
        raise MailError(f"Sending failed: {e}")
    return f"Mail to {msg['To']} sent."


# ── Actions ─────────────────────────────────────────────────────────────────

def _list(p: dict, acc: Account) -> str:
    try:
        limit = int(p.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    unread_only = bool(p.get("unread_only", True))
    items = _fetch(acc, "(UNSEEN)" if unread_only else "ALL", max(1, min(limit, MAX_LISTED)))
    if not items:
        return "No unread mail." if unread_only else "The inbox is empty."
    lines = "\n".join(f"- {i['from']}: {i['subject']}" for i in items)
    return f"{len(items)} {'unread' if unread_only else 'recent'}:\n{lines}"


def _read(p: dict, acc: Account) -> str:
    query = str(p.get("query", "")).strip()
    criterion = f'(OR SUBJECT "{query}" FROM "{query}")' if query else "(UNSEEN)"
    items = _fetch(acc, criterion, 1, with_body=True)
    if not items:
        return f"I found no mail matching '{query}'." if query else "No unread mail."
    i = items[0]
    return (f"From {i['from']}, {i['date']}\nSubject: {i['subject']}\n\n"
            f"{i.get('body') or '(no readable text)'}")


def _search(p: dict, acc: Account) -> str:
    query = str(p.get("query", "")).strip()
    if not query:
        return "What should I search the mailbox for?"
    try:
        limit = int(p.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    items = _fetch(acc, f'(OR SUBJECT "{query}" FROM "{query}")', max(1, min(limit, MAX_LISTED)))
    if not items:
        return f"Nothing in the mailbox matches '{query}'."
    lines = "\n".join(f"- {i['from']}: {i['subject']} ({i['date']})" for i in items)
    return f"Matching '{query}':\n{lines}"


def _draft(p: dict, acc: Account) -> str:
    to = str(p.get("to", "")).strip()
    subject = str(p.get("subject", "")).strip()
    body = str(p.get("body", "")).strip()
    if not body:
        return "What should the mail say?"
    return (f"Draft — to {to or '(no recipient yet)'}, subject '{subject or '(none)'}':\n\n"
            f"{body}\n\nNothing has been sent. Say send it when it reads right.")


def _send(p: dict, acc: Account) -> str:
    to = str(p.get("to", "")).strip()
    subject = str(p.get("subject", "")).strip()
    body = str(p.get("body", "")).strip()
    attachments = p.get("attachments") or []
    if isinstance(attachments, str):
        attachments = [attachments]

    if not to or "@" not in to:
        return "Who should the mail go to? I need a full address."
    if not body:
        return "What should the mail say?"
    if not subject:
        subject = body.split("\n", 1)[0][:60]

    msg = _build(acc, to, subject, body, attachments)   # fails before the gate
                                                        # if an attachment is bad
    detail = f"To: {to}\nSubject: {subject}\n\n{body[:300]}"
    if attachments:
        detail += f"\n\n[{len(attachments)} attachment(s)]"

    return confirm_gate.request(
        key="email_send",
        title=f"Send mail to {to}",
        detail=detail,
        run=lambda: _transmit(acc, msg),
    )


_ACTIONS = {
    "list": _list, "inbox": _list, "unread": _list,
    "read": _read, "open": _read,
    "search": _search, "find": _search,
    "draft": _draft, "compose": _draft,
    "send": _send, "reply": _send,
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "list")).strip().lower() or "list"
    handler = _ACTIONS.get(action)
    if handler is None:
        return (f"I do not know the mail action '{action}'. I can list, read, search, "
                f"draft or send.")
    try:
        result = handler(p, account())
    except MailError as e:
        result = str(e)
    except Exception as e:
        result = f"The mailbox failed: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result.splitlines()[0]}")
        except Exception:
            pass
    return result


# ── Settings form ───────────────────────────────────────────────────────────

def _test(values: dict) -> tuple[bool, str]:
    """Both halves, because a working inbox with a broken SMTP is the failure
    that only shows up at the worst moment."""
    try:
        acc = account(values)
    except MailError as e:
        return False, str(e)

    try:
        conn = _imap(acc)
        conn.select("INBOX")
        typ, data = conn.search(None, "(UNSEEN)")
        unread = len((data[0] or b"").split()) if typ == "OK" else 0
        conn.logout()
    except MailError as e:
        return False, f"IMAP: {e}"

    try:
        context = ssl.create_default_context()
        if acc.smtp_ssl:
            with smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=context, timeout=30) as s:
                s.login(acc.address, acc.password)
        else:
            with smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=30) as s:
                s.starttls(context=context)
                s.login(acc.address, acc.password)
    except Exception as e:
        return False, f"Inbox works, but sending does not: {e}"

    return True, f"Connected. {unread} unread in the inbox."


PLUGIN_SETTINGS = {
    "namespace": NAMESPACE,
    "title": "✉  E-MAIL",
    "fields": [
        {"key": "address", "label": "Mail address", "type": "text",
         "placeholder": "info@reyes-service.de"},
        {"key": "password", "label": "Password (app password with most providers)",
         "type": "password"},
        {"key": "sender_name", "label": "Sender name shown to recipients", "type": "text",
         "placeholder": "Reyes Service"},
        {"key": "imap_host", "label": "IMAP server (blank = detect from the address)",
         "type": "text", "placeholder": "imap.ionos.de"},
        {"key": "imap_port", "label": "IMAP port", "type": "text", "default": "993"},
        {"key": "smtp_host", "label": "SMTP server (blank = detect from the address)",
         "type": "text", "placeholder": "smtp.ionos.de"},
        {"key": "smtp_port", "label": "SMTP port (465 = SSL, 587 = STARTTLS)",
         "type": "text", "default": "465"},
    ],
    "action": {"label": "TEST CONNECTION", "run": _test},
}


PLUGIN = {
    "name": "email",
    "description": (
        "Reads, searches and sends e-mail for the user's own mailbox — new mail, what a "
        "customer wrote, finding an old thread, drafting and sending a reply, with "
        "attachments. Use 'draft' to write something for the user to look at and 'send' "
        "only when they want it to go out; sending puts a confirmation on screen that "
        "they must press. Use send_message instead for WhatsApp or Telegram, and the "
        "whatsapp tool for a spoken voice note."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list (default) | read | search | draft | send"
            },
            "to": {"type": "STRING", "description": "Recipient address when sending"},
            "subject": {"type": "STRING", "description": "Subject line"},
            "body": {"type": "STRING", "description": "The text of the mail, in the user's language"},
            "query": {"type": "STRING", "description": "What to search for in sender or subject"},
            "limit": {"type": "INTEGER", "description": "How many mails to list (default 5, max 10)"},
            "unread_only": {"type": "BOOLEAN", "description": "List only unread mail (default true)"},
            "attachments": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "File paths to attach"
            }
        },
        "required": []
    },
}
