"""
E-Mail — IMAP for reading, SMTP for sending, configured entirely from the
environment.

The desktop plugin (`plugins/email_box.py`) reads its credentials from the
gitignored config file and gates sending behind the desktop HUD. Neither fits a
server, so this is a separate implementation with the same shape: the provider
table and the body extraction are ported (an assistant reading raw HTML markup
aloud is worse than reading nothing), the credentials come from env vars, and
*sending is gated by the server-side approval gate* instead of a HUD button.

Blocking socket work runs in a worker thread; the callers are async.
"""
from __future__ import annotations

import asyncio
import email as email_mod
import email.utils
import imaplib
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage

MAX_BODY_CHARS = 4000
MAX_RESULTS = 25

# Ported from plugins/email_box.py so the desktop and the server guess the same
# servers for the same domain. (host, imap_port, smtp_host, smtp_port, smtp_ssl)
PROVIDERS: dict[str, tuple[str, int, str, int, bool]] = {
    "gmail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, True),
    "googlemail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, True),
    "outlook.com": ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "outlook.de": ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "hotmail.com": ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "hotmail.de": ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "live.de": ("outlook.office365.com", 993, "smtp.office365.com", 587, False),
    "ionos.de": ("imap.ionos.de", 993, "smtp.ionos.de", 465, True),
    "1und1.de": ("imap.ionos.de", 993, "smtp.ionos.de", 465, True),
    "strato.de": ("imap.strato.de", 993, "smtp.strato.de", 465, True),
    "posteo.de": ("posteo.de", 993, "posteo.de", 465, True),
    "mailbox.org": ("imap.mailbox.org", 993, "smtp.mailbox.org", 465, True),
    "yahoo.com": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, True),
    "yahoo.de": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, True),
    "gmx.de": ("imap.gmx.net", 993, "mail.gmx.net", 465, True),
    "gmx.net": ("imap.gmx.net", 993, "mail.gmx.net", 465, True),
    "web.de": ("imap.web.de", 993, "smtp.web.de", 465, True),
}


class MailError(Exception):
    """Something the user needs to hear, phrased plainly."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


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


def load_account() -> Account | None:
    """The configured mailbox, or None when the environment has none."""
    address = _env("EMAIL_USER")
    password = _env("EMAIL_PASSWORD")
    if not address or not password:
        return None
    domain = address.split("@")[-1].lower()
    guess = PROVIDERS.get(domain, (f"imap.{domain}", 993, f"smtp.{domain}", 465, True))
    imap_host = _env("EMAIL_IMAP_HOST") or guess[0]
    imap_port = int(_env("EMAIL_IMAP_PORT") or guess[1])
    smtp_host = _env("EMAIL_SMTP_HOST") or guess[2]
    smtp_port = int(_env("EMAIL_SMTP_PORT") or guess[3])
    smtp_ssl_raw = _env("EMAIL_SMTP_SSL")
    smtp_ssl = (smtp_ssl_raw.lower() in ("1", "true", "yes", "on")) if smtp_ssl_raw else bool(guess[4])
    return Account(address=address, password=password, imap_host=imap_host, imap_port=imap_port,
                   smtp_host=smtp_host, smtp_port=smtp_port, smtp_ssl=smtp_ssl,
                   sender_name=_env("EMAIL_SENDER_NAME"))


def _decode(raw) -> str:
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _body_of(msg) -> str:
    """Plain text when the mail has any, otherwise HTML with the tags removed."""
    def _strip(html: str) -> str:
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

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
                    return _strip(part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"))
                except Exception:
                    continue
        return ""
    try:
        text = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return str(msg.get_payload())
    return _strip(text) if msg.get_content_type() == "text/html" else text


def _attachment_names(msg) -> list[str]:
    names = []
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp and part.get_filename():
                names.append(_decode(part.get_filename()))
    return names


class EmailService:
    """Async facade; every blocking call is pushed to a thread."""

    def __init__(self) -> None:
        self.account = load_account()

    def reload(self) -> None:
        self.account = load_account()

    def configured(self) -> bool:
        return self.account is not None

    def unavailable_reason(self) -> str:
        return "" if self.configured() else "EMAIL_USER / EMAIL_PASSWORD not set"

    def _require(self) -> Account:
        if not self.account:
            raise MailError("No mailbox is configured on this server "
                            "(set EMAIL_USER and EMAIL_PASSWORD).")
        return self.account

    # ── IMAP ─────────────────────────────────────────────────────────────
    def _connect(self, acc: Account) -> imaplib.IMAP4:
        try:
            if acc.imap_port == 143:
                conn: imaplib.IMAP4 = imaplib.IMAP4(acc.imap_host, acc.imap_port)
                conn.starttls(ssl_context=ssl.create_default_context())
            else:
                conn = imaplib.IMAP4_SSL(acc.imap_host, acc.imap_port,
                                         ssl_context=ssl.create_default_context())
        except OSError as e:
            raise MailError(f"Cannot reach the mail server {acc.imap_host}:{acc.imap_port} ({e}).")
        try:
            conn.login(acc.address, acc.password)
        except imaplib.IMAP4.error:
            raise MailError("The mail server rejected the credentials. "
                            "For Gmail or Outlook an app password is usually required.")
        return conn

    def _fetch_sync(self, criterion: str, limit: int, with_body: bool, folder: str) -> list[dict]:
        acc = self._require()
        conn = self._connect(acc)
        try:
            typ, _ = conn.select(folder)
            if typ != "OK":
                raise MailError(f"The mailbox has no folder '{folder}'.")
            typ, data = conn.search(None, criterion)
            if typ != "OK":
                raise MailError("The mail server did not accept that search.")
            ids = (data[0] or b"").split()
            out: list[dict] = []
            for num in reversed(ids[-max(1, min(limit, MAX_RESULTS)):]):
                typ, raw = conn.fetch(num, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email_mod.message_from_bytes(raw[0][1])
                item = {
                    "id": num.decode(errors="replace"),
                    "from": _decode(msg.get("From")),
                    "to": _decode(msg.get("To")),
                    "subject": _decode(msg.get("Subject")) or "(no subject)",
                    "date": _decode(msg.get("Date")),
                    "attachments": _attachment_names(msg),
                }
                if with_body:
                    body = re.sub(r"[ \t]*\n[ \t]*", "\n", _body_of(msg)).strip()
                    item["body"] = body[:MAX_BODY_CHARS]
                    item["truncated"] = len(body) > MAX_BODY_CHARS
                out.append(item)
            return out
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass

    async def recent(self, limit: int = 5, folder: str = "INBOX") -> list[dict]:
        return await asyncio.to_thread(self._fetch_sync, "ALL", limit, False, folder)

    async def unread(self, limit: int = 10, folder: str = "INBOX") -> list[dict]:
        return await asyncio.to_thread(self._fetch_sync, "UNSEEN", limit, False, folder)

    async def search(self, query: str, limit: int = 10, folder: str = "INBOX",
                     with_body: bool = False) -> list[dict]:
        safe = re.sub(r'["\\\r\n]', " ", query or "").strip()
        if not safe:
            return await self.recent(limit, folder)
        criterion = f'(OR OR SUBJECT "{safe}" FROM "{safe}" BODY "{safe}")'
        return await asyncio.to_thread(self._fetch_sync, criterion, limit, with_body, folder)

    async def read(self, query: str, folder: str = "INBOX") -> dict:
        items = await self.search(query, limit=3, folder=folder, with_body=True)
        if not items:
            raise MailError(f"I found no mail matching “{query}”.")
        return items[0]

    # ── SMTP ─────────────────────────────────────────────────────────────
    def build(self, to: str, subject: str, body: str, cc: str = "") -> EmailMessage:
        acc = self._require()
        recipients = [a for a in re.split(r"[,;]\s*", to or "") if a.strip()]
        if not recipients:
            raise MailError("I need at least one recipient address.")
        for addr in recipients:
            if "@" not in addr or " " in addr.strip():
                raise MailError(f"“{addr}” is not a valid e-mail address.")
        msg = EmailMessage()
        msg["From"] = acc.sender()
        msg["To"] = ", ".join(recipients)
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject or "(no subject)"
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid()
        msg.set_content(body or "")
        return msg

    def _send_sync(self, msg: EmailMessage) -> str:
        acc = self._require()
        context = ssl.create_default_context()
        try:
            if acc.smtp_ssl:
                with smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=context, timeout=30) as s:
                    s.login(acc.address, acc.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=30) as s:
                    s.starttls(context=context)
                    s.login(acc.address, acc.password)
                    s.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise MailError("The mail server rejected the credentials when sending.")
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"The mail could not be sent: {e}")
        return msg["To"]

    async def send(self, to: str, subject: str, body: str, cc: str = "") -> dict:
        msg = self.build(to, subject, body, cc)
        recipients = await asyncio.to_thread(self._send_sync, msg)
        return {"sent": True, "to": recipients, "subject": msg["Subject"], "message_id": msg["Message-ID"]}

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": self.unavailable_reason()}

        def probe() -> dict:
            acc = self._require()
            conn = self._connect(acc)
            try:
                typ, data = conn.select("INBOX", readonly=True)
                count = int(data[0]) if typ == "OK" and data and data[0] else 0
                return {"status": "healthy",
                        "detail": f"IMAP {acc.imap_host} ok, {count} messages in INBOX; "
                                  f"SMTP {acc.smtp_host}:{acc.smtp_port}"}
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

        try:
            return await asyncio.to_thread(probe)
        except MailError as e:
            return {"status": "offline", "detail": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"status": "degraded", "detail": str(e)[:200]}
