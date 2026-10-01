"""Lexware Office (öffentliche API): Belege, Buchungskategorien und Kontakte lesen, Belege zur Prüfung anlegen.

Grundlage ist die Dokumentation unter https://developers.lexware.io/docs/ (Stand: gelesen am 2026-09-20).
Was die API NICHT kann, steht hier ausdrücklich, damit niemand etwas vortäuscht: Sie hat keine Umsatzsteuer-
Voranmeldung und keine ELSTER-Übermittlung. Jarvis bereitet Zahlen vor; abgegeben wird in Lexware Office oder
über ELSTER, von einem Menschen.

Zugang: LEXWARE_API_KEY (anlegen unter https://app.lexware.de/addons/public-api). Ohne ihn melden alle Werkzeuge, was
fehlt. Die API erlaubt 2 Anfragen pro Sekunde für alle Endpunkte zusammen; bei HTTP 429 wird mit wachsender
Pause wiederholt.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

BASE_DEFAULT = "https://api.lexware.io/v1"
MIN_INTERVAL = 0.55                      # 2 Anfragen pro Sekunde, mit etwas Luft für Zeitschwankungen
MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".xml": "application/xml"}
VOUCHER_TYPES = ("salesinvoice", "salescreditnote", "purchaseinvoice", "purchasecreditnote")
LIST_TYPES = VOUCHER_TYPES + ("invoice", "downpaymentinvoice", "creditnote", "orderconfirmation", "quotation",
                              "deliverynote")
LIST_STATUS = ("draft", "open", "paid", "paidoff", "voided", "transferred", "sepadebit", "overdue", "accepted",
               "rejected", "unchecked")
TAX_TYPES = ("net", "gross", "vatfree")
TAX_RATES = (0, 7, 19)                   # laut Doku ab 03/2024 die zulässigen Sätze
_UUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_lock: asyncio.Lock | None = None
_last = 0.0


class LexwareError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


async def _throttle() -> None:
    """Ein gemeinsamer Takt für alle Aufrufe im Prozess, damit das Limit auch bei mehreren Agenten hält."""
    global _lock, _last
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        wait = _last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()


def money(v: Any) -> float:
    return round(float(v), 2)


class Lexware:
    def __init__(self) -> None:
        self.key = _env("LEXWARE_API_KEY")
        self.base = (_env("LEXWARE_API_URL") or BASE_DEFAULT).rstrip("/")
        self._transport = None           # nur für Tests

    def configured(self) -> bool:
        return bool(self.key)

    def unavailable_reason(self) -> str:
        return ("" if self.key else
                "LEXWARE_API_KEY fehlt. Schlüssel unter https://app.lexware.de/addons/public-api anlegen und mit "
                "command_center/setup-lexware.sh eintragen.")

    async def _request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                       files: dict | None = None, raw: bool = False) -> Any:
        if not self.configured():
            raise LexwareError(self.unavailable_reason())
        headers = {"Authorization": "Bearer " + self.key, "Accept": "*/*" if raw else "application/json"}
        r = None
        for attempt in range(4):
            await _throttle()
            try:
                async with httpx.AsyncClient(timeout=45, transport=self._transport) as c:
                    r = await c.request(method, self.base + path, params=params, json=json, files=files,
                                        headers=headers)
            except httpx.HTTPError as e:
                raise LexwareError(f"Lexware ist nicht erreichbar ({e.__class__.__name__}).") from e
            if r.status_code != 429:
                break
            await asyncio.sleep(1.0 * (2 ** attempt))
        assert r is not None
        if r.status_code == 429:
            raise LexwareError("Lexware begrenzt gerade die Anfragen (HTTP 429). Später noch einmal versuchen.")
        if r.status_code == 401:
            raise LexwareError("Lexware lehnt den API-Schlüssel ab (LEXWARE_API_KEY prüfen oder neu anlegen).")
        if r.status_code == 403:
            raise LexwareError("Lexware verweigert den Zugriff: fehlende Rechte oder der Tarif enthält die "
                               "Funktion nicht.")
        if r.status_code == 404:
            raise LexwareError("Bei Lexware nicht gefunden.")
        if r.status_code >= 400:
            detail = ""
            try:
                j = r.json()
                detail = str(j.get("message") or j.get("error") or "")
                issues = j.get("IssueList") or j.get("issueList") or []
                if issues:
                    detail += " " + "; ".join(str(i.get("i18nKey") or i.get("source") or i)[:80] for i in issues[:4])
            except ValueError:
                pass
            raise LexwareError(f"Lexware hat die Anfrage abgelehnt (HTTP {r.status_code}). {detail}".strip())
        if raw:
            return r
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise LexwareError("Lexware antwortete unlesbar.") from None

    # ── lesen ──────────────────────────────────────────────────────────────────────────────────────────────────
    async def profile(self) -> dict:
        p = await self._request("GET", "/profile")
        keep = ("companyName", "taxType", "smallBusiness", "businessFeatures", "subscriptionStatus", "organizationId")
        return {k: p[k] for k in keep if k in p} | {"vatRegistrationId": (p.get("company") or {}).get("vatRegistrationId", ""),
                                                    "taxNumber": (p.get("company") or {}).get("taxNumber", "")}

    async def categories(self, kind: str = "", query: str = "") -> list[dict]:
        rows = await self._request("GET", "/posting-categories")
        q = query.strip().lower()
        out = [{"id": c["id"], "name": c["name"], "type": c["type"], "group": c.get("groupName", ""),
                "splitAllowed": c.get("splitAllowed"), "contactRequired": c.get("contactRequired")}
               for c in rows if (not kind or c.get("type") == kind)
               and (not q or q in (c["name"] + " " + c.get("groupName", "")).lower())]
        return out

    async def voucherlist(self, types: str = "any", statuses: str = "any", date_from: str = "", date_to: str = "",
                          contact_id: str = "", number: str = "", page: int = 0, size: int = 100) -> dict:
        for label, val, allowed in (("voucherType", types, LIST_TYPES), ("voucherStatus", statuses, LIST_STATUS)):
            bad = [x for x in val.split(",") if x.strip() and x.strip() != "any" and x.strip() not in allowed]
            if bad:
                raise LexwareError(f"{label}: unbekannter Wert {bad}. Erlaubt: any oder {', '.join(allowed)}.")
        params: dict[str, Any] = {"voucherType": types or "any", "voucherStatus": statuses or "any",
                                  "page": max(0, int(page)), "size": max(1, min(int(size), 250))}
        for name, val in (("voucherDateFrom", date_from), ("voucherDateTo", date_to)):
            if val:
                if not _DATE.match(val):
                    raise LexwareError(f"{name} muss yyyy-MM-dd sein, war: {val!r}")
                params[name] = val
        if contact_id:
            params["contactId"] = contact_id
        if number:
            params["voucherNumber"] = number
        d = await self._request("GET", "/voucherlist", params=params)
        rows = [{"id": v["id"], "type": v.get("voucherType"), "status": v.get("voucherStatus"),
                 "number": v.get("voucherNumber", ""), "date": str(v.get("voucherDate", ""))[:10],
                 "due": str(v.get("dueDate", ""))[:10], "contact": v.get("contactName", ""),
                 "total": v.get("totalAmount"), "open": v.get("openAmount"), "currency": v.get("currency", "EUR"),
                 "archived": v.get("archived")} for v in d.get("content", [])]
        return {"vouchers": rows, "page": d.get("number", 0), "totalPages": d.get("totalPages", 1),
                "totalElements": d.get("totalElements", len(rows))}

    PATHS = {"voucher": "/vouchers", "invoice": "/invoices", "creditnote": "/credit-notes",
             "downpaymentinvoice": "/down-payment-invoices"}

    async def document(self, doc_id: str, kind: str = "voucher") -> dict:
        if not _UUID.match(doc_id or ""):
            raise LexwareError("Die Beleg-ID muss eine UUID sein (aus lexware.vouchers).")
        if kind not in self.PATHS:
            raise LexwareError(f"kind muss eines von {list(self.PATHS)} sein.")
        return await self._request("GET", f"{self.PATHS[kind]}/{doc_id}")

    async def contacts(self, name: str = "", page: int = 0) -> list[dict]:
        params: dict[str, Any] = {"page": max(0, int(page)), "size": 25}
        if name:
            params["name"] = name
        d = await self._request("GET", "/contacts", params=params)
        out = []
        for c in d.get("content", []):
            company = (c.get("company") or {}).get("name", "")
            person = c.get("person") or {}
            out.append({"id": c["id"], "name": company or " ".join(x for x in (person.get("firstName"), person.get("lastName")) if x),
                        "roles": list((c.get("roles") or {}).keys()), "archived": c.get("archived", False)})
        return out

    # ── anlegen ────────────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def check_voucher(v: dict) -> dict:
        """Ein Beleg wird vor dem Senden geprüft: Summen, Steuersätze, Pflichtfelder. Fehler kommen im Klartext."""
        problems = []
        if v.get("type") not in VOUCHER_TYPES:
            problems.append(f"type muss eines von {list(VOUCHER_TYPES)} sein")
        if v.get("taxType") not in TAX_TYPES:
            problems.append(f"taxType muss eines von {list(TAX_TYPES)} sein")
        items = v.get("voucherItems") or []
        if not items:
            problems.append("mindestens eine Position (voucherItems) ist nötig")
        for i, it in enumerate(items, 1):
            if not _UUID.match(str(it.get("categoryId", ""))):
                problems.append(f"Position {i}: categoryId fehlt oder ist keine UUID (aus lexware.categories)")
            if it.get("taxRatePercent") not in TAX_RATES:
                problems.append(f"Position {i}: taxRatePercent muss 0, 7 oder 19 sein")
            if it.get("amount") is None or it.get("taxAmount") is None:
                problems.append(f"Position {i}: amount und taxAmount sind nötig")
        for k in ("voucherDate", "dueDate"):
            if v.get(k) and not _DATE.match(str(v[k])):
                problems.append(f"{k} muss yyyy-MM-dd sein")
        if not v.get("voucherDate"):
            problems.append("voucherDate fehlt")
        if not v.get("useCollectiveContact") and not _UUID.match(str(v.get("contactId", ""))):
            problems.append("contactId (UUID) oder useCollectiveContact=true ist nötig")
        if not problems and items:
            s_amount = money(sum(float(i["amount"]) for i in items))
            s_tax = money(sum(float(i["taxAmount"]) for i in items))
            gross = s_amount + s_tax if v["taxType"] == "net" else s_amount
            if v.get("totalGrossAmount") is None or abs(money(v["totalGrossAmount"]) - money(gross)) > 0.01:
                problems.append(f"totalGrossAmount {v.get('totalGrossAmount')} passt nicht zu den Positionen (erwartet {money(gross)})")
            if v.get("totalTaxAmount") is None or abs(money(v["totalTaxAmount"]) - s_tax) > 0.01:
                problems.append(f"totalTaxAmount {v.get('totalTaxAmount')} passt nicht zu den Positionen (erwartet {s_tax})")
            if v["taxType"] == "vatfree" and s_tax != 0:
                problems.append("bei taxType vatfree darf keine Steuer ausgewiesen sein")
        if problems:
            raise LexwareError("Beleg nicht gesendet, Prüfung fehlgeschlagen: " + "; ".join(problems))
        return v

    async def create_voucher(self, voucher: dict, file: Path | None = None) -> dict:
        v = {k: val for k, val in voucher.items() if val not in (None, "")}
        v.setdefault("voucherStatus", "unchecked")      # zur Prüfung in Lexware, nicht endgültig gebucht
        if v["voucherStatus"] not in ("unchecked", "open"):
            raise LexwareError("voucherStatus darf nur unchecked oder open sein.")
        self.check_voucher(v)
        if file is not None:
            if file.suffix.lower() not in FILE_TYPES:
                raise LexwareError(f"Dateityp {file.suffix} wird nicht angenommen (pdf, png, jpg, xml).")
            if not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
                raise LexwareError("Datei fehlt oder ist größer als 10 MB.")
        created = await self._request("POST", "/vouchers", json=v)
        out = {"id": created.get("id"), "status": v["voucherStatus"], "file_uploaded": False,
               "hinweis": "Als ungeprüfter Beleg in Lexware angelegt. Prüfen und buchen macht ein Mensch."}
        if file is not None:
            try:
                with file.open("rb") as fh:
                    await self._request("POST", f"/vouchers/{created['id']}/files",
                                        files={"file": (file.name, fh.read(), FILE_TYPES[file.suffix.lower()])})
                out["file_uploaded"] = True
            except LexwareError as e:
                out["file_error"] = f"Der Beleg ist angelegt, aber die Datei ging nicht hoch: {e}"
        return out

    # ── Belegdatei aus Lexware holen ───────────────────────────────────────────────────────────────────────────
    MIME_EXT = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "application/xml": ".xml", "text/xml": ".xml"}

    async def download_file(self, file_id: str, dest_dir: Path) -> Path:
        """Die Datei, die an einem Beleg hängt (`files` im Beleg), in den Arbeitsbereich holen, damit man sie lesen kann."""
        if not _UUID.match(file_id or ""):
            raise LexwareError("Die Datei-ID muss eine UUID sein (Feld `files` eines Belegs aus lexware.document).")
        r = await self._request("GET", f"/files/{file_id}", raw=True)
        mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        ext = self.MIME_EXT.get(mime)
        if not ext:
            raise LexwareError(f"Dateityp {mime or 'unbekannt'} wird nicht gelesen (pdf, png, jpg, xml).")
        if len(r.content) > MAX_FILE_BYTES:
            raise LexwareError("Die Datei ist größer als 10 MB.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{file_id}{ext}"
        target.write_bytes(r.content)
        return target

    # ── Ungeprüfte Belege abschließen ("Belege → zu prüfen" in Lexware) ────────────────────────────────────────
    READ_ONLY = ("id", "organizationId", "createdDate", "updatedDate", "resourceUri")
    EDITABLE = ("voucherNumber", "voucherDate", "dueDate", "shippingDate", "totalGrossAmount", "totalTaxAmount", "taxType",
                "contactId", "useCollectiveContact", "remark", "voucherItems")

    async def finalize_voucher(self, voucher_id: str, changes: dict | None = None) -> dict:
        """Einen UNGEPRÜFTEN Beleg vervollständigen und abschließen (unchecked → open).

        Laut Lexware-Doku ist das die einzige Änderung, die die API an einem ungeprüften Beleg erlaubt. Der Beleg wird
        gelesen, mit `changes` zusammengeführt, vollständig geprüft (Summen, Steuersätze, Kategorie) und mit der gelesenen
        Version zurückgeschrieben; bei einem Versionskonflikt (409) wird einmal neu gelesen. „Offen“ heißt hier: noch nicht
        bezahlt. Eine Zahlung (z. B. „per Kasse bezahlt“) setzt die API nicht; das geschieht in Lexware selbst.
        """
        if not _UUID.match(voucher_id or ""):
            raise LexwareError("Die Beleg-ID muss eine UUID sein (aus lexware.vouchers mit statuses=unchecked).")
        bad = [k for k in (changes or {}) if k not in self.EDITABLE]
        if bad:
            raise LexwareError(f"Diese Felder darf das Werkzeug nicht ändern: {bad}. Erlaubt: {list(self.EDITABLE)}.")
        for attempt in range(2):
            cur = await self._request("GET", f"/vouchers/{voucher_id}")
            if cur.get("voucherStatus") != "unchecked":
                raise LexwareError(f"Nur ungeprüfte Belege lassen sich abschließen; dieser hat den Status "
                                   f"'{cur.get('voucherStatus')}'. Gebuchte Belege ändert die API nicht.")
            merged = {k: v for k, v in cur.items() if k not in self.READ_ONLY}
            merged.update({k: v for k, v in (changes or {}).items() if v is not None})
            for k in ("voucherDate", "dueDate", "shippingDate"):      # Lexware liefert Datum+Uhrzeit, verlangt aber yyyy-MM-dd
                if isinstance(merged.get(k), str) and len(merged[k]) > 10:
                    merged[k] = merged[k][:10]
            merged["voucherStatus"] = "open"
            self.check_voucher(merged)
            try:
                await self._request("PUT", f"/vouchers/{voucher_id}", json=merged)
            except LexwareError as e:
                if "409" in str(e) and attempt == 0:      # jemand hat den Beleg inzwischen geändert: neu lesen, noch einmal
                    continue
                raise
            return {"id": voucher_id, "status": "open", "bezahlt": False,
                    "hinweis": "Abgeschlossen und offen (noch nicht bezahlt). Eine Zahlung setzt die API nicht: Bankabgleich oder "
                               "'per Kasse bezahlt' geschieht in Lexware selbst."}
        raise LexwareError("Der Beleg wurde währenddessen geändert (Versionskonflikt). Bitte noch einmal versuchen.")

    # ── Doppelte Belege erkennen ───────────────────────────────────────────────────────────────────────────────
    async def find_duplicates(self, date_from: str, date_to: str, max_documents: int = 1000) -> dict:
        """Belege, die doppelt gebucht aussehen — aus den Listendaten allein, ohne jeden Beleg einzeln zu laden.

        `sicher`: gleiche Seite (Eingang/Ausgang), gleicher Geschäftspartner, gleiche Belegnummer und gleicher Betrag.
        `wahrscheinlich`: gleicher Partner, gleiches Datum, gleicher Betrag, aber andere oder fehlende Nummer.
        Gelöscht wird hier NICHTS: Die Lexware-API kann Belege nicht löschen, und gebuchte Belege dürfen nach GoBD
        nicht gelöscht, nur storniert werden. Jede Gruppe sagt, was noch ungeprüft ist (das darf ein Mensch in
        Lexware entfernen) und was schon gebucht ist (das braucht eine Stornierung/Gutschrift).
        """
        if not (_DATE.match(date_from or "") and _DATE.match(date_to or "")):
            raise LexwareError("date_from und date_to müssen yyyy-MM-dd sein.")
        rows, page = [], 0
        while True:
            d = await self.voucherlist("salesinvoice,salescreditnote,purchaseinvoice,purchasecreditnote,invoice,creditnote,"
                                       "downpaymentinvoice", "any", date_from, date_to, page=page, size=250)
            rows += d["vouchers"]
            page += 1
            if page >= d["totalPages"] or len(rows) >= max_documents:
                break
        rows = [r for r in rows if r["status"] not in ("voided", "draft")]

        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", (s or "").lower())

        def side(t: str) -> str:
            return "eingang" if str(t).startswith("purchase") else "ausgang"

        strong: dict[tuple, list] = {}
        likely: dict[tuple, list] = {}
        for r in rows:
            if r["total"] is None or not r["contact"]:
                continue
            if r["number"]:
                strong.setdefault((side(r["type"]), norm(r["contact"]), norm(r["number"]), money(r["total"])), []).append(r)
            likely.setdefault((side(r["type"]), norm(r["contact"]), r["date"], money(r["total"])), []).append(r)

        def group(kind: str, members: list) -> dict:
            return {"art": kind, "partner": members[0]["contact"], "betrag": members[0]["total"], "datum": members[0]["date"],
                    "belege": [{"id": m["id"], "nummer": m["number"], "status": m["status"], "typ": m["type"]} for m in members],
                    "noch_ungeprueft": [m["id"] for m in members if m["status"] == "unchecked"],
                    "schon_gebucht": [m["id"] for m in members if m["status"] != "unchecked"]}

        seen: set[str] = set()
        out = []
        for members in strong.values():
            if len(members) > 1:
                out.append(group("sicher", members)); seen |= {m["id"] for m in members}
        for members in likely.values():
            fresh = [m for m in members if m["id"] not in seen]
            if len(members) > 1 and len(fresh) > 1:
                out.append(group("wahrscheinlich", members))
        return {"zeitraum": f"{date_from} bis {date_to}", "geprueft": len(rows), "gruppen": out,
                "hinweis": "Nichts wurde gelöscht oder geändert. Lexware-API: kein Löschen von Belegen. Ungeprüfte Belege kann "
                           "ein Mensch in Lexware löschen; gebuchte brauchen eine Stornierung oder Gutschrift."}

    # ── Kontoumsätze offenen Belegen zuordnen ──────────────────────────────────────────────────────────────────
    @staticmethod
    def parse_bank_csv(text: str) -> list[dict]:
        """Ein Kontoauszug als CSV (deutsche Bankexporte: Semikolon, Dezimalkomma). Spalten werden am Namen erkannt."""
        import csv
        import io
        lines = [l for l in text.replace("\ufeff", "").splitlines() if l.strip()]
        if not lines:
            return []
        start = next((i for i, l in enumerate(lines) if re.search(r"(buchungstag|datum|valuta)", l, re.I)
                      and re.search(r"(betrag|umsatz)", l, re.I)), None)
        if start is None:
            raise LexwareError("Keine Kopfzeile mit Datum und Betrag gefunden. Erwartet: CSV mit Spalten wie Buchungstag, "
                               "Betrag, Verwendungszweck.")
        delim = ";" if lines[start].count(";") >= lines[start].count(",") else ","
        rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=delim))

        def col(row, *names):
            for k in row:
                if k and any(n in k.lower() for n in names):
                    return (row[k] or "").strip()
            return ""

        def num(v: str) -> float | None:
            v = v.replace("EUR", "").replace("€", "").replace(" ", "")
            if "," in v:
                v = v.replace(".", "").replace(",", ".")
            try:
                return round(float(v), 2)
            except ValueError:
                return None

        out = []
        for r in rows:
            amount = num(col(r, "betrag", "umsatz"))
            if amount is None:
                continue
            d = col(r, "buchungstag", "buchungsdatum", "datum", "valuta")
            m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", d)
            iso = (f"{m.group(3) if len(m.group(3)) == 4 else '20' + m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
                   if m else d[:10])
            out.append({"date": iso, "amount": amount, "text": col(r, "verwendungszweck", "buchungstext", "beschreibung"),
                        "partner": col(r, "empf", "auftraggeber", "name", "partner", "beguenstigter")})
        return out

    async def match_transactions(self, csv_text: str, date_from: str = "", date_to: str = "") -> dict:
        """Kontoumsätze den OFFENEN Belegen in Lexware zuordnen. Schlägt nur vor, bucht nichts.

        Ausgang (negativ) passt zu Eingangsbelegen, Eingang (positiv) zu Ausgangsrechnungen. `sicher`: Betrag stimmt und
        die Belegnummer steht im Verwendungszweck. `wahrscheinlich`: Betrag stimmt und der Partner stimmt (mindestens ein
        Namensteil). Betrag allein reicht nicht: mehrere Belege mit gleichem Betrag bleiben `unklar`.
        Das Bezahlt-Markieren geht über die Lexware-API nicht; es geschieht in Lexware selbst.
        """
        tx = self.parse_bank_csv(csv_text)
        if date_from:
            tx = [t for t in tx if t["date"] >= date_from]
        if date_to:
            tx = [t for t in tx if t["date"] <= date_to]
        openv, page = [], 0
        while True:
            d = await self.voucherlist("salesinvoice,invoice,purchaseinvoice,downpaymentinvoice", "open,overdue,sepadebit",
                                       page=page, size=250)
            openv += d["vouchers"]
            page += 1
            if page >= d["totalPages"] or len(openv) >= 1000:
                break

        def words(s: str) -> set[str]:
            return {w for w in re.findall(r"[a-z0-9äöüß]{4,}", (s or "").lower())}

        matches, unmatched, used = [], [], set()
        for t in tx:
            side = "purchase" if t["amount"] < 0 else "sales"
            cands = [v for v in openv if v["id"] not in used and v["total"] is not None
                     and abs(abs(t["amount"]) - money(v["total"])) <= 0.01
                     and (v["type"].startswith("purchase") == (side == "purchase"))]
            text = (t["text"] + " " + t["partner"]).lower()
            sure = [v for v in cands if v["number"] and v["number"].lower() in text]
            likely = [v for v in cands if words(v["contact"]) & words(t["partner"] + " " + t["text"])]
            if len(sure) == 1:
                pick, level = sure[0], "sicher"
            elif not sure and len(likely) == 1:
                pick, level = likely[0], "wahrscheinlich"
            elif cands:
                matches.append({"umsatz": t, "art": "unklar", "kandidaten": [{"id": v["id"], "nummer": v["number"], "partner": v["contact"]} for v in cands[:5]]})
                continue
            else:
                unmatched.append(t)
                continue
            used.add(pick["id"])
            matches.append({"umsatz": t, "art": level, "beleg": {"id": pick["id"], "nummer": pick["number"], "partner": pick["contact"], "typ": pick["type"]}})
        return {"umsaetze": len(tx), "zugeordnet": [m for m in matches if m["art"] != "unklar"],
                "unklar": [m for m in matches if m["art"] == "unklar"], "ohne_beleg": unmatched,
                "offene_belege_ohne_zahlung": [{"id": v["id"], "nummer": v["number"], "partner": v["contact"], "betrag": v["total"]}
                                               for v in openv if v["id"] not in used][:50],
                "hinweis": "Vorschläge. Bezahlt-Markieren und Bankabgleich erledigt ein Mensch in Lexware; die API kann das nicht."}

    # ── Auswertung ─────────────────────────────────────────────────────────────────────────────────────────────
    COUNTED =("open", "paid", "paidoff", "overdue", "sepadebit", "transferred")

    async def tax_summary(self, date_from: str, date_to: str, max_documents: int = 300) -> dict:
        """Netto und Steuer je Steuersatz für einen Zeitraum, getrennt nach Umsatz (Ausgang) und Vorsteuer (Eingang).

        Ein ENTWURF nach Belegdatum (Sollversteuerung als Annahme): Er ersetzt weder die Auswertung in Lexware noch
        die Prüfung durch den Steuerberater. Was nicht gelesen werden konnte, steht in `problems`, nichts wird still
        übergangen. Entwürfe, stornierte und ungeprüfte Belege zählen nicht mit; ihre Anzahl steht in `not_counted`.
        """
        if not (_DATE.match(date_from or "") and _DATE.match(date_to or "")):
            raise LexwareError("date_from und date_to müssen yyyy-MM-dd sein.")
        listing, page = [], 0
        while True:
            d = await self.voucherlist("salesinvoice,salescreditnote,purchaseinvoice,purchasecreditnote,invoice,creditnote,"
                                       "downpaymentinvoice", "any", date_from, date_to, page=page, size=250)
            listing += d["vouchers"]
            page += 1
            if page >= d["totalPages"] or len(listing) >= max_documents:
                break
        sales: dict[float, dict] = {}
        purchase: dict[float, dict] = {}
        problems, counted, skipped = [], 0, {}
        for v in listing[:max_documents]:
            if v["status"] not in self.COUNTED:
                skipped[v["status"]] = skipped.get(v["status"], 0) + 1
                continue
            kind = {"invoice": "invoice", "creditnote": "creditnote", "downpaymentinvoice": "downpaymentinvoice"}.get(v["type"], "voucher")
            try:
                doc = await self.document(v["id"], kind)
                lines = self._tax_lines(v["type"], doc)
            except (LexwareError, KeyError, TypeError, ValueError) as e:
                problems.append(f"{v['number'] or v['id']}: {e}")
                continue
            side = purchase if v["type"].startswith("purchase") else sales
            sign = -1 if "credit" in v["type"] else 1
            for rate, net, tax in lines:
                row = side.setdefault(rate, {"net": 0.0, "tax": 0.0, "documents": 0})
                row["net"] = money(row["net"] + sign * net)
                row["tax"] = money(row["tax"] + sign * tax)
                row["documents"] += 1
            counted += 1
        if len(listing) > max_documents:
            problems.append(f"Nur die ersten {max_documents} Belege ausgewertet; der Zeitraum enthält mehr. Kürzer wählen.")
        fmt = lambda side: [{"steuersatz": r, **side[r]} for r in sorted(side)]      # noqa: E731
        return {"zeitraum": f"{date_from} bis {date_to}", "basis": "Belegdatum (Annahme Sollversteuerung)",
                "umsatz_ausgang": fmt(sales), "vorsteuer_eingang": fmt(purchase),
                "summe_umsatzsteuer": money(sum(r["tax"] for r in sales.values())),
                "summe_vorsteuer": money(sum(r["tax"] for r in purchase.values())),
                "zahllast_entwurf": money(sum(r["tax"] for r in sales.values()) - sum(r["tax"] for r in purchase.values())),
                "belege_gezaehlt": counted, "not_counted": skipped, "problems": problems,
                "hinweis": "ENTWURF aus den Lexware-Belegdaten. Nicht übermittelt, nicht geprüft. §13b-Fälle, "
                           "Ist-Versteuerung, Anzahlungen und Korrekturen gesondert prüfen."}

    @staticmethod
    def _tax_lines(vtype: str, doc: dict) -> list[tuple[float, float, float]]:
        """[(Steuersatz, Netto, Steuer)] aus einem Beleg — je nach Art in einer anderen Form."""
        if vtype in VOUCHER_TYPES:
            out = []
            for it in doc["voucherItems"]:
                amount, tax = float(it["amount"]), float(it["taxAmount"])
                net = amount if doc.get("taxType") == "net" else amount - tax
                out.append((float(it["taxRatePercent"]), money(net), money(tax)))
            return out
        return [(float(t["taxRatePercentage"]), money(t["netAmount"]), money(t["taxAmount"])) for t in doc["taxAmounts"]]
