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
    slug: str = "buero"
    label: str = "Büro"

    def sender(self) -> str:
        return (email.utils.formataddr((self.sender_name, self.address))
                if self.sender_name else self.address)


_LABELS = {"buero": "Büro", "privat": "Firma privat", "rechnungen": "Rechnungen"}


def _account_from(prefix: str, slug: str) -> Account | None:
    """Ein Postfach aus den Variablen mit diesem Vorsatz (EMAIL_ oder EMAIL_<KÜRZEL>_)."""
    address = _env(prefix + "USER")
    password = _env(prefix + "PASSWORD")
    if not address or not password:
        return None
    domain = address.split("@")[-1].lower()
    guess = PROVIDERS.get(domain, (f"imap.{domain}", 993, f"smtp.{domain}", 465, True))
    imap_host = _env(prefix + "IMAP_HOST") or guess[0]
    imap_port = int(_env(prefix + "IMAP_PORT") or guess[1])
    smtp_host = _env(prefix + "SMTP_HOST") or guess[2]
    smtp_port = int(_env(prefix + "SMTP_PORT") or guess[3])
    ssl_raw = _env(prefix + "SMTP_SSL")
    smtp_ssl = (ssl_raw.lower() in ("1", "true", "yes", "on")) if ssl_raw else bool(guess[4])
    return Account(address=address, password=password, imap_host=imap_host, imap_port=imap_port,
                   smtp_host=smtp_host, smtp_port=smtp_port, smtp_ssl=smtp_ssl,
                   sender_name=_env(prefix + "SENDER_NAME"), slug=slug,
                   label=_env(prefix + "LABEL") or _LABELS.get(slug, slug.title()))


def load_accounts() -> dict[str, Account]:
    """Alle eingerichteten Postfächer: das Hauptpostfach (EMAIL_USER …) und jedes EMAIL_<KÜRZEL>_USER."""
    out: dict[str, Account] = {}
    main = _account_from("EMAIL_", "buero")
    if main:
        out["buero"] = main
    for key in sorted(os.environ):
        m = re.fullmatch(r"EMAIL_([A-Z0-9]+)_USER", key)
        if m:
            slug = m.group(1).lower()
            acc = _account_from(f"EMAIL_{m.group(1)}_", slug)
            if acc and slug not in out:
                out[slug] = acc
    return out


def load_account() -> Account | None:
    """Das Hauptpostfach (für Altes, das nur eines kennt)."""
    accs = load_accounts()
    return next(iter(accs.values()), None)


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
    """Async facade; every blocking call is pushed to a thread. Kann mehrere Postfächer."""

    def __init__(self) -> None:
        self.accounts = load_accounts()

    def reload(self) -> None:
        self.accounts = load_accounts()

    @property
    def account(self) -> Account | None:
        return next(iter(self.accounts.values()), None)

    def configured(self) -> bool:
        return bool(self.accounts)

    def account_names(self) -> list[str]:
        return list(self.accounts)

    def unavailable_reason(self) -> str:
        return "" if self.configured() else "EMAIL_USER / EMAIL_PASSWORD not set"

    def _pick(self, name: str = "") -> Account:
        """Das gemeinte Postfach. Ohne Angabe nur, wenn es genau eines gibt — nie raten, von wo gesendet wird."""
        if not self.accounts:
            raise MailError("No mailbox is configured on this server "
                            "(set EMAIL_USER and EMAIL_PASSWORD).")
        n = (name or "").strip().lower()
        if n:
            for acc in self.accounts.values():
                if n in (acc.slug, acc.label.lower(), acc.address.lower()) or n in acc.address.lower():
                    return acc
            raise MailError(f"Unknown mailbox “{name}”. Available: {', '.join(self.accounts)}.")
        if len(self.accounts) == 1:
            return next(iter(self.accounts.values()))
        raise MailError("Es sind mehrere Postfächer eingerichtet. Gib das Postfach an "
                        f"({', '.join(self.accounts)}) — bei Antworten das, in dem die Mail angekommen ist.")

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
            raise MailError(f"The mail server rejected the credentials for {acc.address}. "
                            "For Gmail or Outlook an app password is usually required.")
        return conn

    def _fetch_sync(self, acc: Account, criterion: str, limit: int, with_body: bool, folder: str) -> list[dict]:
        conn = self._connect(acc)
        try:
            typ, _ = conn.select(folder, readonly=True)   # nur lesen: nichts wird als „gelesen“ markiert
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
                    "account": acc.slug, "account_label": acc.label,
                    "id": num.decode(errors="replace"),
                    "message_id": (msg.get("Message-ID") or "").strip(),
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

    async def _gather(self, account: str, criterion: str, limit: int, with_body: bool, folder: str) -> list[dict]:
        """Ein Postfach, oder — ohne Angabe — alle, jeweils mit Herkunft."""
        if not self.accounts:
            self._pick(account)   # wirft die passende Fehlermeldung
        accs = [self._pick(account)] if account else list(self.accounts.values())
        results = await asyncio.gather(
            *[asyncio.to_thread(self._fetch_sync, a, criterion, limit, with_body, folder) for a in accs],
            return_exceptions=True)
        out: list[dict] = []
        errors: list[str] = []
        for a, r in zip(accs, results):
            if isinstance(r, Exception):
                errors.append(f"{a.label}: {r}")
            else:
                out.extend(r)
        if not out and errors:
            raise MailError("; ".join(errors))
        return out

    async def recent(self, limit: int = 5, folder: str = "INBOX", account: str = "") -> list[dict]:
        return await self._gather(account, "ALL", limit, False, folder)

    async def unread(self, limit: int = 10, folder: str = "INBOX", account: str = "") -> list[dict]:
        return await self._gather(account, "UNSEEN", limit, False, folder)

    async def search(self, query: str, limit: int = 10, folder: str = "INBOX",
                     with_body: bool = False, account: str = "") -> list[dict]:
        safe = re.sub(r'["\\\r\n]', " ", query or "").strip()
        if not safe:
            return await self.recent(limit, folder, account)
        criterion = f'(OR OR SUBJECT "{safe}" FROM "{safe}" BODY "{safe}")'
        return await self._gather(account, criterion, limit, with_body, folder)

    async def read(self, query: str, folder: str = "INBOX", account: str = "") -> dict:
        items = await self.search(query, limit=3, folder=folder, with_body=True, account=account)
        if not items:
            raise MailError(f"I found no mail matching “{query}”.")
        return items[0]

    # ── SMTP ─────────────────────────────────────────────────────────────
    def build(self, to: str, subject: str, body: str, cc: str = "", account: str = "") -> EmailMessage:
        acc = self._pick(account)
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

    def _send_sync(self, msg: EmailMessage, acc: Account) -> str:
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

    async def send(self, to: str, subject: str, body: str, cc: str = "", account: str = "") -> dict:
        acc = self._pick(account)
        msg = self.build(to, subject, body, cc, account=acc.slug)
        recipients = await asyncio.to_thread(self._send_sync, msg, acc)
        return {"sent": True, "from": acc.address, "account": acc.slug, "to": recipients,
                "subject": msg["Subject"], "message_id": msg["Message-ID"]}

    # ── health ───────────────────────────────────────────────────────────
    async def health(self, slug: str = "") -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": self.unavailable_reason()}
        accs = [self.accounts[slug]] if slug and slug in self.accounts else list(self.accounts.values())
        if slug and slug not in self.accounts:
            return {"status": "not_configured", "detail": f"Postfach „{slug}“ ist nicht eingerichtet"}

        def probe(acc: Account) -> dict:
            conn = self._connect(acc)
            try:
                typ, data = conn.select("INBOX", readonly=True)
                count = int(data[0]) if typ == "OK" and data and data[0] else 0
                return {"status": "healthy",
                        "detail": f"{acc.label}: IMAP {acc.imap_host} ok, {count} messages in INBOX; "
                                  f"SMTP {acc.smtp_host}:{acc.smtp_port}"}
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

        parts: list[dict] = []
        for acc in accs:
            try:
                parts.append(await asyncio.to_thread(probe, acc))
            except MailError as e:
                parts.append({"status": "offline", "detail": f"{acc.label}: {e}"})
            except Exception as e:  # noqa: BLE001
                parts.append({"status": "degraded", "detail": f"{acc.label}: {str(e)[:160]}"})
        worst = ("offline" if any(p["status"] == "offline" for p in parts)
                 else "degraded" if any(p["status"] == "degraded" for p in parts) else "healthy")
        return {"status": worst, "detail": " | ".join(p["detail"] for p in parts)[:400]}
