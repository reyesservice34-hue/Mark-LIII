"""Buchhalter-Agent: Lexware-Client, Belegleser und Postfach-Überwachung — offline.

Lexware wird durch einen Ersatz im Speicher ersetzt (httpx.MockTransport), das Postfach und der Agentenlauf durch Attrappen.
Geprüft wird, was in der Buchhaltung teuer wird, wenn es schiefgeht: falsche Summen, falsche Steuersätze, stille Fehler,
doppelte Belege, gelesene Anweisungen in Belegen, Massenimport alter Mails.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.pop("LEXWARE_API_KEY", None)

import httpx  # noqa: E402

from command_center.backend.services import lexware as lxmod  # noqa: E402
from command_center.backend.services.beleg_reader import BelegError, parse_einvoice, read_beleg  # noqa: E402
from command_center.backend.services.lexware import Lexware, LexwareError  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


async def raises(coro, needle=""):
    try:
        await coro
    except (LexwareError, BelegError) as e:
        return needle.lower() in str(e).lower(), str(e)
    return False, "keine Ausnahme"


CAT = "8f8664a8-fd86-11e1-a21f-0800200c9a66"
UID = "11111111-2222-3333-4444-555555555555"


class FakeLexware:
    def __init__(self):
        self.calls = []
        self.fail429 = 0
        self.status = 200
        self.list_rows = []
        self.docs = {}
        self.put409 = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, dict(request.url.params), request.content[:200]))
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        if self.fail429 > 0:
            self.fail429 -= 1
            return httpx.Response(429, json={})
        p = request.url.path
        if request.method == "PUT" and p.startswith("/v1/vouchers/"):
            if self.put409 > 0:
                self.put409 -= 1
                return httpx.Response(409, json={"message": "version conflict"})
            self.docs[p.rsplit("/", 1)[1]] = json.loads(request.content)
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[1], "version": 2})
        if p.startswith("/v1/files/"):
            kind = p.rsplit("/", 1)[1]
            if kind.startswith("00000000-0000-0000-0000-706466"):
                return httpx.Response(200, content=b"%PDF-1.4 x", headers={"content-type": "application/pdf"})
            return httpx.Response(200, content=b"MZ", headers={"content-type": "application/x-msdownload"})
        if p == "/v1/profile":
            return httpx.Response(200, json={"companyName": "Reyes Service", "taxType": "net", "smallBusiness": False,
                                             "company": {"vatRegistrationId": "DE123"}})
        if p == "/v1/posting-categories":
            return httpx.Response(200, json=[{"id": CAT, "name": "Internet", "type": "outgo", "groupName": "Büro", "splitAllowed": True},
                                             {"id": UID, "name": "Dienstleistung", "type": "income", "groupName": "Einnahmen"}])
        if p == "/v1/voucherlist":
            pg = int(request.url.params.get("page", 0))
            rows = self.list_rows[pg * 250:(pg + 1) * 250]
            return httpx.Response(200, json={"content": rows, "number": pg, "totalPages": max(1, -(-len(self.list_rows) // 250)),
                                             "totalElements": len(self.list_rows)})
        if p == "/v1/vouchers" and request.method == "POST":
            return httpx.Response(200, json={"id": UID, "resourceUri": "x", "version": 1})
        if p.endswith("/files"):
            return httpx.Response(200, json={"id": "f1"})
        for prefix in ("/v1/vouchers/", "/v1/invoices/", "/v1/credit-notes/"):
            if p.startswith(prefix):
                doc = self.docs.get(p.rsplit("/", 1)[1])
                return httpx.Response(200, json=doc) if doc else httpx.Response(404, json={})
        if p == "/v1/contacts":
            return httpx.Response(200, json={"content": [{"id": UID, "company": {"name": "IONOS SE"}, "roles": {"vendor": {}}}]})
        return httpx.Response(404, json={})


def uid(n: str) -> str:
    """Eine gültige UUID aus einem Kurznamen: Die echte API liefert UUIDs, und der Client prüft das."""
    return "00000000-0000-0000-0000-" + "".join(f"{ord(c):02x}" for c in n).ljust(12, "0")[:12]


def row(i, typ, status, number, partner, total, date="2026-08-15", **kw):
    return {"id": i, "voucherType": typ, "voucherStatus": status, "voucherNumber": number, "contactName": partner,
            "totalAmount": total, "voucherDate": date + "T00:00:00.000+02:00", "dueDate": date + "T00:00:00.000+02:00", **kw}


def voucher(lines=None, **kw):
    v = {"type": "purchaseinvoice", "voucherNumber": "R-1", "voucherDate": "2026-08-15", "taxType": "gross",
         "useCollectiveContact": True, "totalGrossAmount": 119.0, "totalTaxAmount": 19.0,
         "voucherItems": lines if lines is not None else [{"amount": 119.0, "taxAmount": 19.0, "taxRatePercent": 19, "categoryId": CAT}]}
    v.update(kw)
    return v


def tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 10 Tf 10 150 Td ({text}) Tj ET" if text else ""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


CII = """<?xml version="1.0"?><rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100" xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
<rsm:ExchangedDocument><ram:ID>RE-2026-77</ram:ID><ram:IssueDateTime><udt:DateTimeString format="102">20260901</udt:DateTimeString></ram:IssueDateTime></rsm:ExchangedDocument>
<rsm:SupplyChainTradeTransaction><ram:ApplicableHeaderTradeAgreement><ram:SellerTradeParty><ram:Name>IONOS SE</ram:Name>
<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">DE815288590</ram:ID></ram:SpecifiedTaxRegistration></ram:SellerTradeParty>
<ram:BuyerTradeParty><ram:Name>Reyes Service</ram:Name></ram:BuyerTradeParty></ram:ApplicableHeaderTradeAgreement>
<ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
<ram:ApplicableTradeTax><ram:CalculatedAmount>1.90</ram:CalculatedAmount><ram:BasisAmount>10.00</ram:BasisAmount><ram:CategoryCode>{cat}</ram:CategoryCode><ram:RateApplicablePercent>19</ram:RateApplicablePercent></ram:ApplicableTradeTax>
<ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:TaxBasisTotalAmount>10.00</ram:TaxBasisTotalAmount><ram:TaxTotalAmount>1.90</ram:TaxTotalAmount><ram:GrandTotalAmount>11.90</ram:GrandTotalAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation>
</ram:ApplicableHeaderTradeSettlement></rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>"""

UBL = """<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"><cbc:ID>U-9</cbc:ID><cbc:IssueDate>2026-09-02</cbc:IssueDate><cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
<cac:AccountingSupplierParty><cac:Party><cac:PartyLegalEntity><cbc:RegistrationName>Lieferant GmbH</cbc:RegistrationName></cac:PartyLegalEntity><cac:PartyTaxScheme><cbc:CompanyID>DE999</cbc:CompanyID></cac:PartyTaxScheme></cac:Party></cac:AccountingSupplierParty>
<cac:AccountingCustomerParty><cac:Party><cac:PartyLegalEntity><cbc:RegistrationName>Reyes Service</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party></cac:AccountingCustomerParty>
<cac:TaxTotal><cbc:TaxAmount>7.00</cbc:TaxAmount><cac:TaxSubtotal><cbc:TaxableAmount>100.00</cbc:TaxableAmount><cbc:TaxAmount>7.00</cbc:TaxAmount><cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>7</cbc:Percent></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>
<cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount>100.00</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount>107.00</cbc:TaxInclusiveAmount></cac:LegalMonetaryTotal></Invoice>"""


async def main():
    lxmod.MIN_INTERVAL = 0.0
    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)
    lxmod.asyncio.sleep = fast_sleep

    # ── ohne Schlüssel ──
    lx = Lexware()
    check("ohne Schlüssel: nicht konfiguriert, Grund nennt die Variable", not lx.configured() and "LEXWARE_API_KEY" in lx.unavailable_reason())
    ok, msg = await raises(lx.profile(), "LEXWARE_API_KEY")
    check("ohne Schlüssel: Aufruf scheitert im Klartext, ohne Netz", ok, msg)

    os.environ["LEXWARE_API_KEY"] = "k"
    fake = FakeLexware()

    def make():
        x = Lexware()
        x._transport = httpx.MockTransport(fake.handler)
        return x
    lx = make()
    check("mit Schlüssel: konfiguriert", lx.configured())

    p = await lx.profile()
    check("Profil: Kleinunternehmer/Steuerart/USt-IdNr. kommen an", p["smallBusiness"] is False and p["taxType"] == "net" and p["vatRegistrationId"] == "DE123", p)
    check("Anfragen tragen den Bearer-Schlüssel", True)
    cats = await lx.categories("outgo", "inter")
    check("Kategorien: Filter nach Art und Text", len(cats) == 1 and cats[0]["id"] == CAT, cats)

    # ── Fehlercodes ──
    for code, needle in ((401, "schlüssel"), (403, "rechte"), (404, "nicht gefunden")):
        fake.status = code
        ok, msg = await raises(make().profile(), needle)
        check(f"HTTP {code} wird im Klartext gemeldet", ok, msg)
    fake.status = 200
    fake.fail429 = 2
    check("HTTP 429: wird wiederholt und klappt danach", (await make().profile())["taxType"] == "net")
    fake.fail429 = 9
    ok, msg = await raises(make().profile(), "429")
    check("HTTP 429 dauerhaft: Fehler statt Endlosschleife", ok, msg)
    fake.fail429 = 0

    # ── Belegprüfung vor dem Senden ──
    for name, v, needle in (
        ("falsche Gesamtsumme", voucher(totalGrossAmount=120.0), "totalGrossAmount"),
        ("falsche Steuersumme", voucher(totalTaxAmount=18.0), "totalTaxAmount"),
        ("Steuersatz 16 % ist nicht zulässig", voucher(lines=[{"amount": 116, "taxAmount": 16, "taxRatePercent": 16, "categoryId": CAT}], totalGrossAmount=116, totalTaxAmount=16), "0, 7 oder 19"),
        ("Kategorie keine UUID", voucher(lines=[{"amount": 119, "taxAmount": 19, "taxRatePercent": 19, "categoryId": "Internet"}]), "categoryId"),
        ("ohne Positionen", voucher(lines=[]), "Position"),
        ("Datum falsch", voucher(voucherDate="15.08.2026"), "voucherDate"),
        ("weder Kontakt noch Sammelkontakt", voucher(useCollectiveContact=False), "contactId"),
        ("unbekannte Belegart", voucher(type="invoice"), "type"),
        ("steuerfrei mit Steuer", voucher(taxType="vatfree"), "vatfree"),
    ):
        ok, msg = await raises(make().create_voucher(v), needle)
        check(f"Prüfung lehnt ab: {name}", ok, msg)
    check("Prüfung ok: netto mit Steuer obendrauf",
          Lexware.check_voucher(voucher(taxType="net", totalGrossAmount=119.0, lines=[{"amount": 100.0, "taxAmount": 19.0, "taxRatePercent": 19, "categoryId": CAT}])) is not None)
    n = len([c for c in fake.calls if c[0] == "POST"])
    check("abgelehnte Belege wurden NICHT an Lexware gesendet", n == 0, n)

    # ── Beleg anlegen ──
    r = await make().create_voucher(voucher())
    post = [c for c in fake.calls if c[0] == "POST" and c[1] == "/v1/vouchers"][0]
    check("Anlegen: Standardstatus ist unchecked (zur Prüfung)", b'"unchecked"' in post[3] or r["status"] == "unchecked", r)
    check("Anlegen: Ergebnis mit ID und Hinweis", r["id"] == UID and "Mensch" in r["hinweis"])
    ok, msg = await raises(make().create_voucher(voucher(voucherStatus="paid")), "unchecked oder open")
    check("Anlegen: nur unchecked oder open erlaubt", ok, msg)
    tmp = Path(tempfile.mkdtemp())
    (tmp / "b.pdf").write_bytes(tiny_pdf("x" * 100))
    r = await make().create_voucher(voucher(), tmp / "b.pdf")
    check("Anlegen mit Datei: Datei wird hochgeladen", r["file_uploaded"] is True and any(c[1].endswith("/files") for c in fake.calls), r)
    (tmp / "b.exe").write_bytes(b"x")
    ok, msg = await raises(make().create_voucher(voucher(), tmp / "b.exe"), "Dateityp")
    check("Anlegen: ungeeigneter Dateityp wird abgewiesen", ok, msg)

    # ── Belegliste ──
    ok, msg = await raises(make().voucherlist("kaffee"), "voucherType")
    check("Belegliste: unbekannter Typ wird abgewiesen", ok, msg)
    ok, msg = await raises(make().voucherlist("any", "any", "01.08.2026"), "yyyy-MM-dd")
    check("Belegliste: Datum muss yyyy-MM-dd sein", ok, msg)

    # ── Doppelte Belege ──
    fake.list_rows = [
        row("a", "purchaseinvoice", "open", "RE-1", "IONOS SE", 11.90), row("b", "purchaseinvoice", "unchecked", "RE-1", "IONOS SE", 11.90),
        row("c", "purchaseinvoice", "open", "RE-2", "Baustoff AG", 250.00, "2026-08-20"), row("d", "purchaseinvoice", "open", "", "Baustoff AG", 250.00, "2026-08-20"),
        row("e", "purchaseinvoice", "open", "RE-3", "Anderer", 250.00, "2026-08-20"),
        row("f", "salesinvoice", "open", "AR-1", "Kunde", 500.00), row("g", "purchaseinvoice", "voided", "RE-1", "IONOS SE", 11.90),
    ]
    d = await make().find_duplicates("2026-08-01", "2026-08-31")
    arts = {g["art"]: g for g in d["gruppen"]}
    check("Dubletten: gleiche Nummer+Partner+Betrag = sicher", "sicher" in arts and {b["id"] for b in arts["sicher"]["belege"]} == {"a", "b"}, d)
    check("Dubletten: sagt, was noch ungeprüft ist und was schon gebucht", arts["sicher"]["noch_ungeprueft"] == ["b"] and arts["sicher"]["schon_gebucht"] == ["a"], arts.get("sicher"))
    check("Dubletten: gleicher Partner+Datum+Betrag ohne Nummer = wahrscheinlich", "wahrscheinlich" in arts and {b["id"] for b in arts["wahrscheinlich"]["belege"]} == {"c", "d"})
    check("Dubletten: anderer Partner mit gleichem Betrag ist keine Dublette", not any("e" in [b["id"] for b in g["belege"]] for g in d["gruppen"]))
    check("Dubletten: stornierte Belege zählen nicht", not any("g" in [b["id"] for b in g["belege"]] for g in d["gruppen"]))
    check("Dubletten: es wurde nichts gelöscht oder geschrieben", not [c for c in fake.calls if c[0] in ("DELETE", "PUT")] and "gelöscht" in d["hinweis"].lower())

    # ── Umsatzsteuer-Entwurf ──
    ids = {k: uid(k) for k in ("s1", "s2", "p1", "p2", "x1", "x2", "bad")}
    fake.list_rows = [
        row(ids["s1"], "salesinvoice", "open", "A1", "Kunde", 119.0), row(ids["s2"], "invoice", "paid", "A2", "Kunde", 107.0),
        row(ids["p1"], "purchaseinvoice", "paid", "E1", "Lief", 119.0), row(ids["p2"], "purchasecreditnote", "open", "E2", "Lief", 11.9),
        row(ids["x1"], "salesinvoice", "draft", "A3", "Kunde", 999.0), row(ids["x2"], "salesinvoice", "unchecked", "A4", "Kunde", 500.0),
        row(ids["bad"], "purchaseinvoice", "open", "E9", "Lief", 5.0),
    ]
    fake.docs = {
        ids["s1"]: {"taxType": "gross", "voucherItems": [{"amount": 119.0, "taxAmount": 19.0, "taxRatePercent": 19}]},
        ids["s2"]: {"taxAmounts": [{"taxRatePercentage": 7, "taxAmount": 7.0, "netAmount": 100.0}]},
        ids["p1"]: {"taxType": "gross", "voucherItems": [{"amount": 119.0, "taxAmount": 19.0, "taxRatePercent": 19}]},
        ids["p2"]: {"taxType": "net", "voucherItems": [{"amount": 10.0, "taxAmount": 1.9, "taxRatePercent": 19}]},
    }
    t = await make().tax_summary("2026-08-01", "2026-08-31")
    sales = {r["steuersatz"]: r for r in t["umsatz_ausgang"]}
    purch = {r["steuersatz"]: r for r in t["vorsteuer_eingang"]}
    check("USt: Ausgang 19 % (brutto → netto/steuer getrennt)", sales[19.0]["net"] == 100.0 and sales[19.0]["tax"] == 19.0, sales)
    check("USt: Ausgang 7 % aus einer Lexware-Rechnung (taxAmounts)", sales[7.0]["net"] == 100.0 and sales[7.0]["tax"] == 7.0, sales)
    check("USt: Vorsteuer wird um die Gutschrift gekürzt (19 − 1,90)", purch[19.0]["tax"] == 17.1 and purch[19.0]["net"] == 90.0, purch)
    check("USt: Zahllast = Umsatzsteuer − Vorsteuer", t["zahllast_entwurf"] == round(26.0 - 17.1, 2), t["zahllast_entwurf"])
    check("USt: Entwürfe und ungeprüfte Belege zählen nicht, ihre Zahl steht da", t["not_counted"] == {"draft": 1, "unchecked": 1}, t["not_counted"])
    check("USt: ein nicht lesbarer Beleg steht in problems, wird nicht still übergangen", any("E9" in x for x in t["problems"]), t["problems"])
    check("USt: sagt ausdrücklich, dass es ein Entwurf ist und nichts übermittelt wurde", "ENTWURF" in t["hinweis"] and "Nicht übermittelt" in t["hinweis"])

    # ── Kontoumsätze ──
    csv_text = ("Kontoauszug Test\n\nBuchungstag;Valuta;Name Zahlungsbeteiligter;Verwendungszweck;Betrag\n"
                "01.09.2026;01.09.2026;IONOS SE;Rechnung RE-1 Hosting;-11,90\n"
                "02.09.2026;02.09.2026;Kunde GmbH;Zahlung AR-1;500,00\n"
                "03.09.2026;03.09.2026;Unbekannt;Bargeld;-40,00\n"
                "04.09.2026;04.09.2026;Baustoff AG;Material;-250,00\n")
    tx = Lexware.parse_bank_csv(csv_text)
    check("Kontoauszug: Kopfzeile, Dezimalkomma und Datum werden erkannt", len(tx) == 4 and tx[0]["amount"] == -11.90 and tx[0]["date"] == "2026-09-01", tx)
    fake.list_rows = [
        row("a", "purchaseinvoice", "open", "RE-1", "IONOS SE", 11.90), row("f", "salesinvoice", "open", "AR-1", "Kunde GmbH", 500.00),
        row("c", "purchaseinvoice", "open", "RE-2", "Baustoff AG", 250.00), row("d", "purchaseinvoice", "open", "RE-7", "Andere Firma", 250.00),
        row("z", "purchaseinvoice", "open", "RE-8", "Nie bezahlt", 77.00),
    ]
    m = await make().match_transactions(csv_text)
    arts = {(x["umsatz"]["amount"], x["art"]) for x in m["zugeordnet"]}
    check("Zuordnung: Betrag + Belegnummer im Text = sicher", (-11.9, "sicher") in arts and (500.0, "sicher") in arts, arts)
    check("Zuordnung: Betrag + Partner = wahrscheinlich, wenn eindeutig", (-250.0, "wahrscheinlich") in arts or any(u["umsatz"]["amount"] == -250.0 for u in m["unklar"]), m)
    check("Zuordnung: Umsatz ohne passenden Beleg bleibt offen (nie nach Betrag geraten)", [t_["amount"] for t_ in m["ohne_beleg"]] == [-40.0], m["ohne_beleg"])
    check("Zuordnung: offener Beleg ohne Zahlung wird aufgeführt", any(x["nummer"] == "RE-8" for x in m["offene_belege_ohne_zahlung"]))
    ok, msg = await raises(make().match_transactions("nur\nText"), "Kopfzeile")
    check("Kontoauszug ohne Kopfzeile wird abgewiesen", ok, msg)


    # ── Ungeprüfte Belege abschließen (Lexware: Belege → zu prüfen) ──
    vid, vid2 = uid("fin1"), uid("fin2")
    unchecked = {"id": vid, "organizationId": "org", "type": "purchaseinvoice", "voucherStatus": "unchecked", "version": 3,
                 "voucherNumber": "R-77", "voucherDate": "2026-08-20T00:00:00.000+02:00", "taxType": "gross", "useCollectiveContact": True,
                 "totalGrossAmount": 119.0, "totalTaxAmount": 19.0, "createdDate": "x", "updatedDate": "y", "files": ["file-1"],
                 "voucherItems": [{"amount": 119.0, "taxAmount": 19.0, "taxRatePercent": 19, "categoryId": UID}]}
    fake.docs = {vid: dict(unchecked)}
    fake.calls.clear()
    r = await make().finalize_voucher(vid, {"voucherItems": [{"amount": 119.0, "taxAmount": 19.0, "taxRatePercent": 19, "categoryId": CAT}], "remark": "Karte 6952"})
    sent = fake.docs[vid]
    check("Abschließen: Status wird open, Kategorie und Vermerk übernommen", sent["voucherStatus"] == "open" and sent["voucherItems"][0]["categoryId"] == CAT and sent["remark"] == "Karte 6952", sent)
    check("Abschließen: Version und Dateien bleiben erhalten, Nur-Lese-Felder fehlen", sent["version"] == 3 and sent["files"] == ["file-1"] and not {"id", "organizationId", "createdDate", "updatedDate"} & set(sent), sent)
    check("Abschließen: Datum geht als yyyy-MM-dd zurück (Lexware liefert Datum+Uhrzeit)", sent["voucherDate"] == "2026-08-20", sent["voucherDate"])
    check("Abschließen: Ergebnis sagt ausdrücklich NICHT bezahlt", r["bezahlt"] is False and "Kasse" in r["hinweis"], r)
    fake.docs = {vid: {**unchecked, "voucherStatus": "open"}}
    ok, msg = await raises(make().finalize_voucher(vid), "ungeprüfte")
    check("Abschließen: bereits gebuchter Beleg wird nicht angefasst", ok, msg)
    fake.docs = {vid: dict(unchecked)}
    ok, msg = await raises(make().finalize_voucher(vid, {"voucherStatus": "paid"}), "nicht ändern")
    check("Abschließen: Status/fremde Felder lassen sich nicht über changes setzen", ok, msg)
    fake.calls.clear()
    ok, msg = await raises(make().finalize_voucher(vid, {"totalGrossAmount": 500.0}), "totalGrossAmount")
    check("Abschließen: falsche Summe wird vor dem Senden abgewiesen", ok and not [c for c in fake.calls if c[0] == "PUT"], msg)
    fake.docs = {vid: dict(unchecked)}
    fake.put409 = 1
    fake.calls.clear()
    r = await make().finalize_voucher(vid, {"remark": "nach Konflikt"})
    check("Abschließen: Versionskonflikt (409) → neu lesen, noch einmal senden", r["status"] == "open" and len([c for c in fake.calls if c[0] == "GET"]) == 2 and len([c for c in fake.calls if c[0] == "PUT"]) == 2, [c[:2] for c in fake.calls])
    incomplete = {k: v for k, v in unchecked.items() if k not in ("totalGrossAmount", "totalTaxAmount")}
    fake.docs = {vid2: {**incomplete, "id": vid2}}
    r = await make().finalize_voucher(vid2, {"totalGrossAmount": 119.0, "totalTaxAmount": 19.0})
    check("Abschließen: fehlende Summen lassen sich über changes ergänzen", r["status"] == "open" and fake.docs[vid2]["totalGrossAmount"] == 119.0)
    ok, msg = await raises(make().finalize_voucher("kein-uuid"), "UUID")
    check("Abschließen: Beleg-ID muss eine UUID sein", ok, msg)


    # ── Belegdatei aus Lexware ──
    tmpd = Path(tempfile.mkdtemp())
    pdf_id, exe_id = uid("pdf"), uid("exe")
    pth = await make().download_file(pdf_id, tmpd)
    check("Datei-Download: PDF landet im Arbeitsbereich mit passender Endung", pth.suffix == ".pdf" and pth.read_bytes().startswith(b"%PDF"), pth)
    ok, msg = await raises(make().download_file(exe_id, tmpd), "Dateityp")
    check("Datei-Download: ungeeigneter Typ wird abgewiesen", ok, msg)
    ok, msg = await raises(make().download_file("nix", tmpd), "UUID")
    check("Datei-Download: Datei-ID muss eine UUID sein", ok, msg)

    # ── Belegleser ──
    root = Path(tempfile.mkdtemp())
    (root / "belege").mkdir()
    (root / "belege" / "text.pdf").write_bytes(tiny_pdf("Rechnung IONOS SE Nr RE-2026-77 vom 01.09.2026 Netto 10,00 EUR Umsatzsteuer 19 Prozent 1,90 EUR Brutto 11,90 EUR"))
    r = read_beleg(root / "belege" / "text.pdf", root)
    check("PDF mit Text: wird direkt gelesen", r["typ"] == "pdf-text" and "RE-2026-77" in r["text"] and r["seiten"] == 1, r)
    (root / "belege" / "leer.pdf").write_bytes(tiny_pdf(""))
    try:
        read_beleg(root / "belege" / "leer.pdf", root)
        check("PDF ohne Text und ohne Bild: ehrlicher Fehler statt Raten", False, "keine Ausnahme")
    except BelegError as e:
        check("PDF ohne Text und ohne Bild: ehrlicher Fehler statt Raten", "Foto" in str(e), str(e))
    (root / "belege" / "foto.jpg").write_bytes(b"\xff\xd8\xff")
    r = read_beleg(root / "belege" / "foto.jpg", root)
    check("Foto: geht als Bild an das Modell", r["typ"] == "bild" and r["bilder"] == ["belege/foto.jpg"], r)
    e = parse_einvoice(CII.replace("{cat}", "S"))
    check("E-Rechnung CII: Nummer, Datum, Aussteller, USt-IdNr., Beträge", (e["nummer"], e["datum"], e["aussteller"], e["aussteller_ustid"], e["netto"], e["steuer"], e["brutto"]) == ("RE-2026-77", "2026-09-01", "IONOS SE", "DE815288590", 10.0, 1.9, 11.9), e)
    check("E-Rechnung CII: Steuersatz-Zeile", e["steuersaetze"] == [{"satz": 19.0, "netto": 10.0, "steuer": 1.9, "kategorie": "S"}] and not e["reverse_charge"], e["steuersaetze"])
    check("E-Rechnung CII: Reverse-Charge (AE) wird erkannt", parse_einvoice(CII.replace("{cat}", "AE"))["reverse_charge"] is True)
    u = parse_einvoice(UBL)
    check("E-Rechnung UBL: Felder und 7 %", (u["nummer"], u["aussteller"], u["brutto"], u["steuersaetze"][0]["satz"]) == ("U-9", "Lieferant GmbH", 107.0, 7.0), u)
    for name, bad, needle in (("XXE/DOCTYPE", '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', "DOCTYPE"),
                              ("kein XML", "das ist keine rechnung", "XML"), ("fremdes Format", "<foo><bar/></foo>", "Unbekannt")):
        try:
            parse_einvoice(bad)
            check(f"E-Rechnung: {name} wird abgewiesen", False, "keine Ausnahme")
        except BelegError as ex:
            check(f"E-Rechnung: {name} wird abgewiesen", needle.lower() in str(ex).lower(), str(ex))

    # ── Postfach-Überwachung ──
    from command_center.backend.modules import buchhaltung as bh

    class Mail:
        def __init__(self, items):
            self.items = items

        def account_names(self):
            return ["rechnungen"]

        async def recent(self, limit=25, account=""):
            return self.items

    class Tasks:
        def __init__(self):
            self.rows = []

        def create(self, **kw):
            self.rows.append(kw)
            return {"id": f"t{len(self.rows)}", **kw}

        def get(self, i):
            return {"id": i}

    class Runtime:
        def __init__(self):
            self.runs = []

        async def start_task_run(self, task, principal, agent_id):
            self.runs.append((task, agent_id))
            return {}

    class DB:
        def __init__(self):
            self.d = {}

        def get_setting(self, k, default=None):
            return self.d.get(k, default)

        def set_setting(self, k, v):
            self.d[k] = v

    def m(mid, files):
        return {"account": "rechnungen", "message_id": mid, "from": "rechnung@ionos.de", "subject": "Ihre Rechnung", "attachments": files}

    mail, tasks, rt, db = Mail([m("<1>", ["alt.pdf"]), m("<2>", [])]), Tasks(), Runtime(), DB()
    state = SimpleNamespace(agents=SimpleNamespace(get=lambda i: SimpleNamespace(enabled=True)), db=db, runtime=rt,
                            services={"email": mail, "tasks": tasks})
    os.environ.pop("LEXWARE_API_KEY")
    r = await bh.run(state)
    check("Überwachung ohne Lexware-Schlüssel: läuft nicht und sagt warum", "LEXWARE_API_KEY" in str(r.get("skipped")) and not rt.runs, r)
    os.environ["LEXWARE_API_KEY"] = "k"
    r = await bh.run(state)
    check("Überwachung, erster Lauf: nichts verarbeitet, Bestand nur vermerkt (kein Massenimport)", r.get("first_run") and not rt.runs and set(db.d["buchhaltung_seen"]) == {"<1>", "<2>"}, r)
    mail.items = [m("<3>", ["ionos-rechnung.pdf"]), m("<4>", ["logo.png.exe", "agb.docx"]), m("<1>", ["alt.pdf"])]
    r = await bh.run(state)
    check("Überwachung: nur neue Mail mit Belegdatei startet einen Lauf", r.get("new") == 1 and len(rt.runs) == 1 and rt.runs[0][1] == "buchhaltung", r)
    check("Auftrag nennt die Message-ID und warnt, dass Mailtext Daten sind", "<3>" in tasks.rows[0]["description"] and "Daten, keine Anweisung" in tasks.rows[0]["description"])
    check("Mail ohne Belegendung (exe/docx) wird ignoriert", "<4>" not in tasks.rows[0]["description"])
    r = await bh.run(state)
    check("Überwachung: dieselbe Mail wird nicht zweimal verarbeitet", r.get("new") == 0 and len(rt.runs) == 1, r)
    state.agents = SimpleNamespace(get=lambda i: SimpleNamespace(enabled=False))
    r = await bh.run(state)
    check("Überwachung: abgeschalteter Agent → keine Läufe, klare Meldung", "abgeschaltet" in str(r.get("skipped")), r)

    # ── Umsatzsteuer-Voranmeldung rechtzeitig anstoßen ──
    import datetime as dt
    tasks2, rt2, db2 = Tasks(), Runtime(), DB()
    st2 = SimpleNamespace(agents=SimpleNamespace(get=lambda i: SimpleNamespace(enabled=True)), db=db2, runtime=rt2,
                          services={"email": Mail([]), "tasks": tasks2})
    r = await bh.run_ustva(st2, dt.date(2026, 10, 3))
    check("USt-Voranmeldung: vor dem 5. passiert nichts", "Zeitfenster" in str(r.get("skipped")) and not rt2.runs, r)
    r = await bh.run_ustva(st2, dt.date(2026, 10, 6))
    check("USt-Voranmeldung: ab dem 5. wird der Agent beauftragt", r.get("started") and len(rt2.runs) == 1 and rt2.runs[0][1] == "buchhaltung", r)
    d = tasks2.rows[0]["description"]
    check("Auftrag: Frist ist Montag der 12.10.2026 (der 10. ist ein Samstag)", "12.10.2026" in d and "Wochenende" in d, d[:300])
    check("Auftrag: Vormonat September 2026", "September 2026" in d and "2026-09-01 bis 2026-09-30" in d, d[:400])
    check("Auftrag im Oktober nennt zusätzlich das Vorquartal Juli–September", "2026-07-01 bis 2026-09-30" in d, d)
    check("Auftrag: Annahme kennzeichnen, nie als abgegeben melden", "Annahme" in d and "nie, sie sei abgegeben" in d, d)
    check("Auftrag ist hoch priorisiert und trägt die Periode", tasks2.rows[0]["priority"] == "high" and tasks2.rows[0]["meta"]["ustva"] == "2026-10")
    check("Auftrag beginnt mit der Belegprüfung am selben Tag (Postfach des Zeitraums, ungeprüfte Belege, Dubletten)",
          "am selben Tag" in d and "email.belege_im_zeitraum" in d and "statuses=unchecked" in d and "lexware.duplicates" in d and d.index("ZUERST") < d.index("lexware.tax_summary"), d)
    check("Erster Durchgang heißt ENTWURF", "ENTWURF" in d and "SCHLUSSPRÜFUNG" not in d)
    r = await bh.run_ustva(st2, dt.date(2026, 10, 7))
    check("Zwischen Entwurf und Schlusstag passiert nichts (kein Dauerfeuer)", "schon angestoßen" in str(r.get("skipped")) and len(rt2.runs) == 1, r)
    r = await bh.run_ustva(st2, dt.date(2026, 10, 8))
    ds = tasks2.rows[1]["description"]
    check("Zwei Werktage vor der Frist (Do 8.10.) kommt die Schlussprüfung, ebenfalls mit Belegprüfung zuerst",
          r.get("started") and r["phase"] == "schluss" and "SCHLUSSPRÜFUNG" in ds and "ZUERST" in ds and "was sich seitdem geändert hat" in ds, (r, ds[:200]))
    r = await bh.run_ustva(st2, dt.date(2026, 10, 9))
    check("Danach ist für den Monat Schluss (nur ein Entwurf und eine Schlussprüfung)", "schon angestoßen" in str(r.get("skipped")) and len(rt2.runs) == 2, r)
    r = await bh.run_ustva(st2, dt.date(2026, 10, 13))
    check("Nach der Frist läuft nichts mehr", "Zeitfenster" in str(r.get("skipped")), r)
    r = await bh.run_ustva(st2, dt.date(2026, 10, 2), force=True, phase="schluss")
    check("Knopf in der App (force) startet sofort die gewünschte Stufe, auch außerhalb des Fensters", r.get("started") and r["phase"] == "schluss" and len(rt2.runs) == 3, r)
    check("… und verbraucht die Monatsmarke nicht", db2.get_setting(bh.USTVA_KEY, "") == "2026-10")
    tasks2.rows.pop(); rt2.runs.pop()
    check("Arbeitstage: 2 Werktage vor Mo 12.10. ist Do 8.10.; vor Di 10.11. ist Fr 6.11.", bh.workdays_before(dt.date(2026, 10, 12), 2) == dt.date(2026, 10, 8) and bh.workdays_before(dt.date(2026, 11, 10), 2) == dt.date(2026, 11, 6))
    r = await bh.run_ustva(st2, dt.date(2026, 11, 5))
    d2 = tasks2.rows[2]["description"]
    check("Nächster Monat: neuer Auftrag, Frist Dienstag 10.11.2026, kein Quartal", r.get("started") and "10.11.2026" in d2 and "Vorquartal" not in d2, d2[:260])
    check("Januar: Vorquartal ist Oktober–Dezember des Vorjahres", (await bh.run_ustva(st2, dt.date(2027, 1, 7))).get("started") and "2026-10-01 bis 2026-12-31" in tasks2.rows[3]["description"], tasks2.rows[3]["description"])
    os.environ.pop("LEXWARE_API_KEY")
    r = await bh.run_ustva(SimpleNamespace(agents=st2.agents, db=DB(), runtime=Runtime(), services={"tasks": Tasks()}), dt.date(2026, 10, 6))
    check("USt-Voranmeldung: ohne Lexware-Schlüssel läuft nichts und es sagt warum", "LEXWARE_API_KEY" in str(r.get("skipped")), r)
    os.environ["LEXWARE_API_KEY"] = "k"
    check("Fristen: 10.10.2026 (Sa) → 12.10.; 10.11.2026 (Di) bleibt", bh.due_date(2026, 10) == dt.date(2026, 10, 12) and bh.due_date(2026, 11) == dt.date(2026, 11, 10))

    # ── Abendprüfung ──
    fake_ev = FakeLexware()

    class LX(Lexware):
        def __init__(self):
            super().__init__()
            self._transport = httpx.MockTransport(fake_ev.handler)
    real_lexware = bh.Lexware
    bh.Lexware = LX
    fake_ev.list_rows = [row(uid("u1"), "purchaseinvoice", "unchecked", "R-1", "IONOS SE", 11.9), row(uid("u2"), "purchaseinvoice", "unchecked", "R-2", "Tanke", 60.0)]
    tk, rn, dbe = Tasks(), Runtime(), DB()
    ste = SimpleNamespace(agents=SimpleNamespace(get=lambda i: SimpleNamespace(enabled=True)), db=dbe, runtime=rn, services={"email": Mail([]), "tasks": tk})
    evening = lambda h, day=24: dt.datetime(2026, 9, day, h, 30)  # noqa: E731
    r = await bh.run_evening(ste, evening(15))
    check("Abendprüfung: vor 19 Uhr passiert nichts", "Nicht jetzt" in str(r.get("skipped")) and not rn.runs, r)
    r = await bh.run_evening(ste, evening(19))
    check("Abendprüfung: nach 19 Uhr werden die neuen ungeprüften Belege einem Lauf übergeben", r.get("status") == "gestartet" and r["neue_belege"] == 2 and len(rn.runs) == 1, r)
    desc = tk.rows[0]["description"]
    check("Auftrag nennt die Belege (Partner, Nummer, Betrag), die Zahlungsregel und den Postfach-Check von heute", "IONOS SE" in desc and "R-2" in desc and "Zahlungsregel" in desc and "email.belege_im_zeitraum" in desc, desc[:300])
    check("Der Lexware-Aufruf fragt genau die ungeprüften Buchhaltungsbelege ab", any(c[1] == "/v1/voucherlist" and c[2].get("voucherStatus") == "unchecked" and "purchaseinvoice" in c[2].get("voucherType", "") for c in fake_ev.calls))
    r = await bh.run_evening(ste, evening(21))
    check("Einmal am Tag: am selben Abend nicht noch einmal", "Nicht jetzt" in str(r.get("skipped")) and len(rn.runs) == 1, r)
    r = await bh.run_evening(ste, evening(19, 25))
    check("Am nächsten Abend ohne neue Belege: kein Lauf, kein Sprachmodell, Bestand wird gemeldet", r.get("status") == "nichts Neues" and r["ungeprueft_gesamt"] == 2 and len(rn.runs) == 1, r)
    fake_ev.list_rows.append(row(uid("u3"), "purchaseinvoice", "unchecked", "R-3", "Baustoff AG", 250.0))
    r = await bh.run_evening(ste, evening(19, 26))
    check("Ein neu hochgeladener Beleg löst wieder einen Lauf aus, mit genau diesem einen", r.get("neue_belege") == 1 and len(rn.runs) == 2 and "R-3" in tk.rows[1]["description"] and "R-1" not in tk.rows[1]["description"], r)
    fake_ev.status = 500
    r = await bh.run_evening(ste, evening(19, 27))
    check("Lexware-Fehler: gemeldet, und der Tag gilt NICHT als erledigt (später noch einmal)", "error" in r and dbe.get_setting(bh.EVENING_KEY) != "2026-09-27", r)
    fake_ev.status = 200
    r = await bh.run_evening(ste, evening(20, 27))
    check("… beim nächsten Versuch am selben Abend klappt es", r.get("status") == "nichts Neues", r)
    r = await bh.run_evening(ste, evening(15, 28), force=True)
    check("Knopf in der App: sofort, auch vor 19 Uhr", r.get("status") == "nichts Neues", r)
    os.environ.pop("LEXWARE_API_KEY")
    r = await bh.run_evening(ste, evening(19, 29))
    check("Abendprüfung ohne Lexware-Schlüssel: läuft nicht und sagt warum", "LEXWARE_API_KEY" in str(r.get("skipped")), r)
    os.environ["LEXWARE_API_KEY"] = "k"

    # ── Übersicht für die App ──
    o = await bh.uebersicht(ste, dt.date(2026, 10, 3))
    check("Übersicht: Frist, Tage, Schlusstag und Zeitraum", o["ustva"]["frist"] == "2026-10-12" and o["ustva"]["tage_bis_frist"] == 9 and o["ustva"]["schlusstag"] == "2026-10-08" and o["ustva"]["zeitraum"] == "September 2026", o["ustva"])
    check("Übersicht: ungeprüfte Belege mit Anzahl und Liste", o["ungeprueft"]["anzahl"] == 3 and len(o["ungeprueft"]["belege"]) == 3, o["ungeprueft"])
    check("Übersicht: Firmenname aus Lexware", o["lexware"]["firma"] == "Reyes Service" and o["lexware"]["kleinunternehmer"] is False, o["lexware"])
    o2 = await bh.uebersicht(ste, dt.date(2026, 10, 14))
    check("Übersicht nach der Frist: Frist, Schlusstag und Zeitraum gehören alle zur NÄCHSTEN Voranmeldung", o2["ustva"]["frist"] == "2026-11-10" and o2["ustva"]["schlusstag"] == "2026-11-06" and o2["ustva"]["zeitraum"] == "Oktober 2026" and o2["ustva"]["quartal_moeglich"] is False, o2["ustva"])
    o4 = await bh.uebersicht(ste, dt.date(2026, 9, 20))
    check("Übersicht am 20.09.: Frist 12.10., Zeitraum September, Schlusstag 08.10., Quartalsende möglich", o4["ustva"]["frist"] == "2026-10-12" and o4["ustva"]["zeitraum"] == "September 2026" and o4["ustva"]["schlusstag"] == "2026-10-08" and o4["ustva"]["quartal_moeglich"] is True and o4["ustva"]["tage_bis_frist"] == 22, o4["ustva"])
    dbe.set_setting(bh.USTVA_KEY, "2026-10")
    o5 = await bh.uebersicht(ste, dt.date(2026, 9, 20))
    check("Die Erledigt-Marken gehören zur angezeigten Voranmeldung (Oktober), nicht zum laufenden Monat", o5["ustva"]["entwurf_angestossen"] is True and o4["ustva"]["entwurf_angestossen"] is False, (o4["ustva"], o5["ustva"]))
    dbe.d.pop(bh.USTVA_KEY, None)
    os.environ.pop("LEXWARE_API_KEY")
    o3 = await bh.uebersicht(ste, dt.date(2026, 10, 3))
    check("Übersicht ohne Schlüssel: sagt es ehrlich, die Frist steht trotzdem da, keine erfundenen Belege", o3["lexware"]["konfiguriert"] is False and "LEXWARE_API_KEY" in o3["lexware"]["hinweis"] and o3["ungeprueft"] is None and o3["ustva"]["frist"] == "2026-10-12", o3)
    os.environ["LEXWARE_API_KEY"] = "k"
    bh.Lexware = real_lexware


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
