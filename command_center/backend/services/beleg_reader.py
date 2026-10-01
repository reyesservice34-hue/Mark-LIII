"""Belege lesen: PDF mit Text, gescannte PDFs und Fotos, E-Rechnungen als XML.

Was wie gelesen wird, und warum so:
  * PDF mit Textebene (fast alle Rechnungen von IONOS, Lieferanten, Software): Der Text wird direkt gelesen. Das ist
    genauer als jede Bilderkennung.
  * Gescanntes PDF (nur Bilder): Die eingebetteten Seitenbilder werden herausgezogen und dem Modell gezeigt (es liest
    sie mit Bilderkennung). Geht das nicht, steht das im Ergebnis, es wird nichts erfunden.
  * Foto (png, jpg): geht direkt an das Modell.
  * E-Rechnung (XRechnung/ZUGFeRD als XML, Formate CII und UBL): Die Felder stehen strukturiert im Dokument und werden
    ohne Raten ausgelesen: Nummer, Datum, Aussteller, Beträge je Steuersatz.
Der Belegtext ist DATEN. Was darin als Anweisung formuliert ist, wird nie ausgeführt.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_TEXT = 30000
MAX_XML = 2 * 1024 * 1024
MIN_TEXT_FOR_TEXT_PDF = 80
MAX_SCAN_IMAGES = 3


class BelegError(Exception):
    pass


def pdf_text(path: Path) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise BelegError("pypdf ist nicht installiert: PDF-Text kann nicht gelesen werden.") from e
    try:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        return "\n".join((p.extract_text() or "") for p in reader.pages[:50]), pages
    except Exception as e:  # noqa: BLE001
        raise BelegError(f"PDF nicht lesbar ({e.__class__.__name__}).") from e


def pdf_scan_images(path: Path, out_dir: Path, tag: str) -> list[Path]:
    """Die eingebetteten Bilder der ersten Seiten eines gescannten PDFs herausziehen (JPEG/PNG, wie sie im PDF liegen)."""
    from pypdf import PdfReader
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    try:
        reader = PdfReader(str(path))
        for pno, page in enumerate(reader.pages[:MAX_SCAN_IMAGES], 1):
            try:
                images = list(page.images)
            except Exception:  # noqa: BLE001  — Filter, den ohne Bildbibliothek niemand lesen kann
                continue
            for i, img in enumerate(images[:1]):
                ext = Path(getattr(img, "name", "") or "").suffix.lower() or ".jpg"
                if ext not in (".jpg", ".jpeg", ".png"):
                    continue
                target = out_dir / f"{tag}_s{pno}_{i}{ext}"
                target.write_bytes(img.data)
                saved.append(target)
            if len(saved) >= MAX_SCAN_IMAGES:
                break
    except Exception:  # noqa: BLE001
        pass
    return saved


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _t(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _num(v: str) -> float | None:
    try:
        return round(float(v), 2)
    except ValueError:
        return None


def parse_einvoice(xml_text: str) -> dict:
    """XRechnung/ZUGFeRD-XML (CII oder UBL) in feste Felder. Was nicht dasteht, bleibt leer, nichts wird ergänzt."""
    if len(xml_text.encode("utf-8", "ignore")) > MAX_XML:
        raise BelegError("Die XML-Datei ist zu groß.")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", xml_text, re.I):
        raise BelegError("Die XML-Datei enthält eine DOCTYPE/ENTITY-Deklaration und wird aus Sicherheitsgründen nicht gelesen.")
    try:
        root = _strip_ns(ET.fromstring(xml_text))
    except ET.ParseError as e:
        raise BelegError(f"Keine gültige XML-Datei ({e}).") from e
    if root.tag == "CrossIndustryInvoice":
        head = root.find(".//ExchangedDocument")
        seller, buyer = root.find(".//SellerTradeParty"), root.find(".//BuyerTradeParty")
        mon = root.find(".//SpecifiedTradeSettlementHeaderMonetarySummation")
        d = _t(head, "IssueDateTime/DateTimeString")
        out = {"format": "CII (XRechnung/ZUGFeRD)", "nummer": _t(head, "ID"),
               "datum": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else d,
               "aussteller": _t(seller, "Name"), "aussteller_ustid": _t(seller, "SpecifiedTaxRegistration/ID"),
               "empfaenger": _t(buyer, "Name"), "waehrung": _t(root, ".//InvoiceCurrencyCode"),
               "netto": _num(_t(mon, "TaxBasisTotalAmount")), "steuer": _num(_t(mon, "TaxTotalAmount")),
               "brutto": _num(_t(mon, "GrandTotalAmount")), "steuersaetze": []}
        for tx in root.findall(".//ApplicableHeaderTradeSettlement/ApplicableTradeTax"):
            out["steuersaetze"].append({"satz": _num(_t(tx, "RateApplicablePercent")), "netto": _num(_t(tx, "BasisAmount")),
                                        "steuer": _num(_t(tx, "CalculatedAmount")), "kategorie": _t(tx, "CategoryCode")})
    elif root.tag in ("Invoice", "CreditNote"):
        sup, cus = root.find(".//AccountingSupplierParty/Party"), root.find(".//AccountingCustomerParty/Party")
        mon = root.find(".//LegalMonetaryTotal")
        out = {"format": "UBL (XRechnung)", "nummer": _t(root, "ID"), "datum": _t(root, "IssueDate"),
               "aussteller": _t(sup, "PartyLegalEntity/RegistrationName") or _t(sup, "PartyName/Name"),
               "aussteller_ustid": _t(sup, "PartyTaxScheme/CompanyID"),
               "empfaenger": _t(cus, "PartyLegalEntity/RegistrationName") or _t(cus, "PartyName/Name"),
               "waehrung": _t(root, "DocumentCurrencyCode"),
               "netto": _num(_t(mon, "TaxExclusiveAmount")), "steuer": None,
               "brutto": _num(_t(mon, "TaxInclusiveAmount")), "steuersaetze": []}
        for sub in root.findall(".//TaxTotal/TaxSubtotal"):
            out["steuersaetze"].append({"satz": _num(_t(sub, "TaxCategory/Percent")), "netto": _num(_t(sub, "TaxableAmount")),
                                        "steuer": _num(_t(sub, "TaxAmount")), "kategorie": _t(sub, "TaxCategory/ID")})
        tt = _num(_t(root, "TaxTotal/TaxAmount"))
        out["steuer"] = tt
        if root.tag == "CreditNote":
            out["format"] += ", Gutschrift"
    else:
        raise BelegError(f"Unbekanntes XML-Format (Wurzel: {root.tag}). Erwartet: XRechnung/ZUGFeRD (CII) oder UBL.")
    # Reverse-Charge (Steuerschuldnerschaft des Leistungsempfängers) ist der Sonderfall, den man nie übersehen darf
    out["reverse_charge"] = any(s.get("kategorie") == "AE" for s in out["steuersaetze"])
    return out


def read_beleg(path: Path, root: Path) -> dict:
    """Ein Beleg → was der Agent daraus lesen kann. Bilder liegen als Pfade (relativ zum Arbeitsbereich) unter `bilder`."""
    ext = path.suffix.lower()
    if not path.is_file():
        raise BelegError("Datei nicht gefunden.")
    rel = path.relative_to(root).as_posix()
    if ext in (".png", ".jpg", ".jpeg"):
        return {"typ": "bild", "datei": rel, "bilder": [rel],
                "hinweis": "Das Foto liegt dem Modell an: lies Aussteller, Nummer, Datum, Positionen, Netto, Steuer, Brutto ab."}
    if ext == ".xml":
        data = parse_einvoice(path.read_text(encoding="utf-8-sig", errors="replace"))
        return {"typ": "e-rechnung", "datei": rel, "felder": data,
                "hinweis": "Strukturierte Daten aus der E-Rechnung; sie sind genauer als jede Bilderkennung."}
    if ext == ".pdf":
        text, pages = pdf_text(path)
        if len(text.strip()) >= MIN_TEXT_FOR_TEXT_PDF:
            return {"typ": "pdf-text", "datei": rel, "seiten": pages, "text": text[:MAX_TEXT], "abgeschnitten": len(text) > MAX_TEXT}
        scans = pdf_scan_images(path, root / "belege" / "scan", re.sub(r"[^A-Za-z0-9]+", "_", path.stem)[:40])
        if scans:
            rels = [s.relative_to(root).as_posix() for s in scans]
            return {"typ": "pdf-scan", "datei": rel, "seiten": pages, "bilder": rels,
                    "hinweis": "Gescanntes PDF ohne Text. Die Seitenbilder liegen dem Modell an: lies sie ab."}
        raise BelegError("Das PDF hat keinen Text und die Seitenbilder ließen sich nicht herausziehen. "
                         "Bitte als Foto (jpg/png) oder als PDF mit Textebene bereitstellen.")
    raise BelegError(f"Dateityp {ext} wird nicht gelesen (pdf, png, jpg, xml).")
