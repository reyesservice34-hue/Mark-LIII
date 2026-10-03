"""
Dateisicherheit — was eine Datei im Arbeitsbereich wirklich ist, bevor sie dort landet.

Ausgeführt wurde eine hochgeladene Datei hier nie automatisch: terminal.execute ist admin-only,
risk="critical" und läuft nie von selbst auf einem Pfad, den ein Upload gewählt hat (siehe
orchestrator/builtin_tools.py). Die eigentliche Lücke war eine andere: niemand prüfte, ob der
tatsächliche Inhalt einer Datei zu ihrem Namen passt, ob ein Office-Dokument ein Makro mitbringt,
oder ob ein Archiv einen Pfad trägt, der bei einem künftigen Auspacken aus dem Zielordner ausbräche.
Das prüft dieses Modul — lesend, bevor eine Datei gespeichert wird.

Bewusst ohne externe Antiviren-Engine (keine ist auf diesem Server installiert): Was hier erkannt
wird, ist Signatur-/Struktur-basiert (Magic Bytes, ZIP-Einträge, XML-DOCTYPE), kein vollständiger
Virenscan. Das wird nirgends als mehr ausgegeben, als es ist.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

RISK_ORDER = ("low", "medium", "high")

# Erweiterungen, die auf diesem Server keinen legitimen Geschäftszweck haben (Belege, Fotos, Office-
# Dokumente, Text/Code — keine Installationspakete oder eigenständig ausführbaren Dateien).
DANGEROUS_EXT = {
    ".exe", ".dll", ".com", ".scr", ".msi", ".msp", ".bat", ".cmd", ".ps1", ".ps2", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".jar", ".app", ".apk", ".run", ".bin",
    ".reg", ".cpl", ".gadget", ".hta",
}
MACRO_EXT = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm"}
IMAGE_SIGNATURE = {"jpeg": (".jpg", ".jpeg"), "png": (".png",), "gif": (".gif",), "webp": (".webp",)}
_SHEBANG = re.compile(rb"^#!\s*/\S*\b(sh|bash|dash|zsh|python[0-9.]*|perl|ruby|node)\b")
_VBA_NAMES = ("word/vbaProject.bin", "xl/vbaProject.bin", "ppt/vbaProject.bin")


def sniff(head: bytes) -> str:
    """Grober Dateityp aus den ersten Bytes — unabhängig vom Namen, den niemand fälschungssicher macht."""
    if head[:2] == b"MZ":
        return "exe"
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"):
        return "macho"
    if _SHEBANG.match(head):
        return "script"
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return "zip"
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.lstrip()[:5] in (b"<?xml", b"<?XML"):
        return "xml"
    return ""


def _zip_findings(path: Path | None, data: bytes | None) -> list[str]:
    """ZIP-basierte Formate (zip, docx/xlsx/pptx, ...) von innen prüfen: Pfad-Flucht, Makro, grobe Bombe."""
    flags: list[str] = []
    try:
        zf = zipfile.ZipFile(path) if path is not None else zipfile.ZipFile(io.BytesIO(data or b""))
    except (zipfile.BadZipFile, OSError):
        return ["Als ZIP erkannt, aber nicht lesbar — beschädigt oder kein echtes ZIP."]
    try:
        names = zf.namelist()
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                flags.append(f"Archiv enthält einen Pfad, der aus dem Zielordner ausbrechen würde: {name}")
                break
        if any(n in names for n in _VBA_NAMES):
            flags.append("Office-Datei enthält ein Makro (VBA).")
        total_un = sum(i.file_size for i in zf.infolist())
        total_comp = sum(max(1, i.compress_size) for i in zf.infolist())
        if total_un > 50 * 1024 * 1024 and total_un / total_comp > 100:
            flags.append("Ungewöhnlich hohes Kompressionsverhältnis — möglicherweise eine ZIP-Bombe.")
    finally:
        zf.close()
    return flags


def classify(name: str, *, data: bytes | None = None, path: Path | None = None) -> dict:
    """Risiko einer Datei einschätzen, bevor sie gespeichert/behalten wird.

    `data` (die vollen Bytes, für Upload-Prüfung vor dem Schreiben) oder `path` (eine bereits auf der
    Platte liegende Datei) übergeben — mindestens eines von beiden.
    """
    head = (data or b"")[:4096] if data is not None else (path.open("rb").read(4096) if path else b"")
    detected = sniff(head)
    ext = Path(name).suffix.lower()
    flags: list[str] = []
    risk = "low"

    def bump(level: str) -> None:
        nonlocal risk
        if RISK_ORDER.index(level) > RISK_ORDER.index(risk):
            risk = level

    if detected in ("exe", "elf", "macho", "script"):
        kind = {"exe": "eine Windows-Programmdatei", "elf": "ein Linux-Programm", "macho": "ein macOS-Programm",
               "script": "ein ausführbares Skript (Shebang)"}[detected]
        flags.append(f"Der Dateiinhalt ist {kind}, unabhängig vom Namen „{name}“.")
        bump("high")
    elif ext in DANGEROUS_EXT:
        flags.append(f"Dateiendung {ext} gilt in diesem Arbeitsbereich als potenziell ausführbar.")
        bump("high")

    if ext in MACRO_EXT:
        flags.append(f"Dateiendung {ext} ist eine makrofähige Office-Variante.")
        bump("medium")

    if detected == "zip":
        for f in _zip_findings(path, data if path is None else None):
            flags.append(f)
            bump("high" if "ausbrechen" in f else "medium")

    if detected == "xml" or ext == ".xml":
        text = (data if data is not None else (path.read_bytes() if path else b"")).decode("utf-8", errors="replace")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", text[:4096], re.I):
            flags.append("XML enthält eine DOCTYPE/ENTITY-Deklaration (möglicher XXE-Versuch).")
            bump("high")

    # Inhalt passt nicht zur behaupteten Endung — kein Risiko für sich, aber erwähnenswert.
    for sig, exts in IMAGE_SIGNATURE.items():
        if ext in exts and detected and detected in IMAGE_SIGNATURE and detected != sig:
            flags.append(f"Inhalt sieht nach {detected} aus, der Name behauptet {ext}.")
            bump("medium")
            break
    if ext == ".pdf" and detected and detected != "pdf":
        flags.append(f"Inhalt ist kein PDF, obwohl die Datei auf .pdf endet (erkannt: {detected}).")
        bump("medium" if detected not in ("exe", "elf", "macho", "script") else "high")

    return {"risk": risk, "flags": flags, "detected": detected or "unbekannt"}
