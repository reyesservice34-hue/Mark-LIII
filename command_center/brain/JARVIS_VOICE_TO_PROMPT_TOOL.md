# MIA VOICE-TO-PROMPT TOOL
## Das Zwischenglied zwischen Pauls Stimme und Mias Gehirn

**Version:** 1.0  
**Status:** OPTIONAL – Nur aktivieren, wenn Performance-Gewinn messbar  
**Position:** Zwischen STT (Faster-Whisper) und Mia Core  
**Performance-Ziel:** < 200ms zusätzliche Latenz  

---

## WARUM DIESES TOOL?

**Problem:** Paul spricht natürlich, mit Füllwörtern, Abschweifungen, halbfertigen Sätzen:
> „Äh, also morgen, die Baustelle in Karben, wer kommt da mit? Und, äh, haben wir das Material? Ich glaube der Christoph muss ja mitgenommen werden..."

**Ohne Tool:** Mia muss das alles selbst interpretieren → mehr Tokens, mehr Zeit, mehr Fehlerrisiko

**Mit Tool:** Der Prompt wird vorstrukturiert:
```
INTENT: Baustellenplanung
DATUM: 2026-09-22 (morgen)
ORT: Karben
FRAGEN:
  1. Personalzuweisung (wer kommt mit?)
  2. Material-Status
  3. Christoph-Transport (kein Führerschein)
AGENT: Operations
PRIORITÄT: Hoch (morgen)
KONTEXT_LADEN: calendar.read(2026-09-22), abteilungen/betrieb/kontext.md
```

→ Mia weiß sofort, was zu tun ist. Kein Rätselraten.

---

## ARCHITEKTUR

```
PAUL SPRICHT
    ↓
FASTER-WHISPER (STT)
    ↓
[ROHTEXT: "Äh, also morgen, die Baustelle in Karben..."]
    ↓
┌─────────────────────────────────────────┐
│  VOICE-TO-PROMPT TOOL                   │
│  ─────────────────────                  │
│  Modell: LOCAL (qwen2.5:7b) oder CHEAP  │
│  Latenz-Ziel: < 200ms                   │
│                                         │
│  1. Bereinigen (Füllwörter raus)        │
│  2. Intent erkennen                     │
│  3. Entitäten extrahieren               │
│  4. Agent zuordnen                      │
│  5. Kontext-Bedarf bestimmen            │
│  6. Strukturierten Prompt bauen         │
└─────────────────────────────────────────┘
    ↓
[STRUKTURIERTER PROMPT]
    ↓
JARVIS CORE (Sonnet 5)
    ↓
AGENT (Operations)
    ↓
ANTWORT
    ↓
TTS (Seraphina)
    ↓
PAUL HÖRT
```

---

## SCHRITT 1: BEREINIGEN

**Entfernen:**
- Füllwörter: „äh", „ähm", „also", „quasi", „sozusagen", „halt", „ne"
- Wiederholungen: „die die Baustelle" → „die Baustelle"
- Selbstkorrekturen: „morgen, nein übermorgen" → „übermorgen"
- Abschweifungen ohne Bezug

**Behalten:**
- Alle Entitäten (Namen, Orte, Daten, Zahlen)
- Alle Fragen
- Alle Anweisungen
- Emotionale Marker (wenn relevant: „dringend", „wichtig", „egal")

**Beispiel:**
```
VORHER: "Äh, also morgen, die Baustelle in Karben, wer kommt da mit? Und, äh, haben wir das Material? Ich glaube der Christoph muss ja mitgenommen werden..."

NACHHER: "Morgen Baustelle Karben: Wer kommt mit? Material vorhanden? Christoph muss mitgenommen werden."
```

---

## SCHRITT 2: INTENT ERKENNEN

**Intent-Kategorien:**
| Intent | Trigger-Wörter | Agent |
|--------|----------------|-------|
| BAUSTELLENPLANUNG | Baustelle, Einsatz, wer kommt, Team, Fahrzeug | Operations |
| KALKULATION | Angebot, Preis, kalkulieren, kostet, Aufmaß | Kalkulation |
| KUNDENKOMMUNIKATION | Kunde, Mail, schreiben, antworten, Anfrage | Vertrieb |
| RECHNUNG | Rechnung, Abschlag, Forderung, bezahlt, Mahnung | Finanzen |
| MATERIAL | Material, bestellen, Lieferant, Preis vergleichen | Einkauf |
| MARKETING | Website, Social, Post, Bild, Werbung | Marketing |
| SYSTEM | Server, Fehler, Mia, App, Dashboard, Bug | Engineering |
| PERSONAL | Ziel, Training, privat, Wochenplan, ich will | Mentor |
| KALENDER | Termin, verschieben, wann, Kalender, frei | Operations |
| STATUS | Wie läuft, Status, Übersicht, was ist los | Core (Daily Mode) |
| LERNEN | Merk dir, speichern, notieren, ab jetzt | Core (Memory) |

**Bei Mehrdeutigkeit:** Beide Intents auflisten, Mia Core entscheidet.

---

## SCHRITT 3: ENTITÄTEN EXTRAHIEREN

**Entitätstypen:**
```
DATUM:      morgen → 2026-09-22, nächste Woche → KW39, Montag → 2026-09-22
ZEIT:       um 8 → 08:00, nachmittags → 14:00-17:00
ORT:        Karben, Berlin, Steinfurth, Reichelsheim
PERSON:     Paul, Christoph, Jürgen, Bernd, [Kundenname]
FAHRZEUG:   Vivaro, Bipper, Privat
MATERIAL:   Sperrholz, Fliesen, Farbe, [Produkt]
BETRAG:     500 Euro → 500.00 EUR
PROJEKT:    Sanierung Müller, Bad Schmidt
DOKUMENT:   Angebot 2026-09-15, Rechnung RE-2026-042
```

**Auflösung relativer Angaben:**
- „morgen" → aktuelles Datum + 1
- „nächste Woche" → KW + 1
- „der Kunde" → letzter erwähnter Kunde (aus Working Memory)
- „das Projekt" → aktuelles Projekt (aus Working Memory)

---

## SCHRITT 4: AGENT ZUORDNEN

**Regel:** Ein Intent → Ein Agent (primär). Bei Mehrfach-Intents: Reihenfolge nach Abhängigkeit.

**Beispiel:**
```
"Kalkulier das Bad für Müller und schick ihm das Angebot"

INTENT 1: KALKULATION → Agent Kalkulation
INTENT 2: KUNDENKOMMUNIKATION → Agent Vertrieb
ABHÄNGIGKEIT: Intent 2 braucht Ergebnis von Intent 1
REIHENFOLGE: Kalkulation → Vertrieb
FREIGABE: Intent 2 = Level 2 (Versand) → Paul fragen
```

---

## SCHRITT 5: KONTEXT-BEDARF BESTIMMEN

**Was muss Mia laden, bevor sie antwortet?**

| Intent | Kontext laden |
|--------|---------------|
| BAUSTELLENPLANUNG | calendar.read(datum), abteilungen/betrieb/kontext.md, /projects/[projekt]/ |
| KALKULATION | /business/finances/preise.md, abteilungen/vertrieb/kontext.md, /projects/[projekt]/aufmaß.md |
| KUNDENKOMMUNIKATION | memory.search(kunde), /business/clients/[kunde].md, email.search(kunde) |
| RECHNUNG | /projects/[projekt]/angebot.md, /business/finances/, lexware.* |
| MATERIAL | /business/suppliers/, memory.search(material) |
| SYSTEM | /system/tools.md, /system/integrations.md, logs |
| PERSONAL | /personal/goals.md, /personal/routines.md |
| STATUS | calendar.read(heute), task.list(offen), email.search(ungelesen) |

**Nicht alles laden.** Nur was der Intent braucht. Spart Tokens.

---

## SCHRITT 6: STRUKTURIERTEN PROMPT BAUEN

**Output-Format:**
```yaml
INTENT: [Primär-Intent]
SECONDARY_INTENTS: [Liste, falls vorhanden]
AGENT: [Primär-Agent]
PRIORITY: [LOW/MEDIUM/HIGH/URGENT]
ENTITIES:
  DATUM: [ISO-Datum]
  ORT: [Ort]
  PERSON: [Liste]
  PROJEKT: [Projekt-ID]
  [weitere...]
QUESTIONS:
  1. [Konkrete Frage 1]
  2. [Konkrete Frage 2]
INSTRUCTIONS:
  1. [Konkrete Anweisung 1]
CONTEXT_LOAD:
  - [Datei/Tool 1]
  - [Datei/Tool 2]
APPROVAL_LEVEL: [0/1/2/3]
ORIGINAL_TEXT: "[Bereinigter Text]"
```

**Beispiel komplett:**
```yaml
INTENT: BAUSTELLENPLANUNG
SECONDARY_INTENTS: [MATERIAL]
AGENT: Operations
PRIORITY: HIGH
ENTITIES:
  DATUM: 2026-09-22
  ORT: Karben
  PERSON: [Christoph]
  PROJEKT: [unbekannt – aus Kalender ermitteln]
QUESTIONS:
  1. Wer kommt zur Baustelle Karben am 2026-09-22?
  2. Ist das Material vorhanden?
  3. Wer nimmt Christoph mit (kein Führerschein)?
INSTRUCTIONS: []
CONTEXT_LOAD:
  - calendar.read(2026-09-22)
  - abteilungen/betrieb/kontext.md
  - /projects/[aus Kalender]/material.md
APPROVAL_LEVEL: 0
ORIGINAL_TEXT: "Morgen Baustelle Karben: Wer kommt mit? Material vorhanden? Christoph muss mitgenommen werden."
```

---

## PERFORMANCE-REGELN

**Das Tool darf NICHT:**
- ❌ Mehr als 200ms Latenz hinzufügen
- ❌ Sonnet 5 verwenden (zu teuer für Vorverarbeitung)
- ❌ Selbst Antworten generieren (nur strukturieren)
- ❌ Kontext laden (nur bestimmen, was geladen werden soll)
- ❌ Bei einfachen Befehlen aktiv werden („Ja", „Nein", „Erledigt", „Danke")

**Das Tool MUSS:**
- ✅ Lokal laufen (Ollama qwen2.5:7b) oder CHEAP (Gemini Flash)
- ✅ Bei Timeout (> 500ms) → Bypass, Rohtext direkt an Mia
- ✅ Bei Fehler → Bypass, Rohtext direkt an Mia
- ✅ Messbar sein: Latenz, Token-Ersparnis, Fehlerrate loggen

**Bypass-Bedingungen (Tool wird übersprungen):**
- Text < 5 Wörter
- Text ist reine Bestätigung („Ja", „Ok", „Mach das")
- Text ist Fortsetzung eines laufenden Dialogs (Working Memory aktiv)
- Tool-Latenz > 500ms (3× in Folge → Tool deaktivieren, Paul informieren)

---

## MESSUNG: LOHNT SICH DAS TOOL?

**Vor Aktivierung 1 Woche messen:**
```
OHNE TOOL:
  - Durchschnittliche Antwortzeit: [X] s
  - Durchschnittliche Tokens pro Anfrage: [Y]
  - Fehlerrate (falscher Agent, falsches Verständnis): [Z] %

MIT TOOL:
  - Durchschnittliche Antwortzeit: [X'] s (inkl. Tool-Latenz)
  - Durchschnittliche Tokens pro Anfrage: [Y'] (Sonnet-Tokens)
  - Fehlerrate: [Z'] %
  - Tool-Latenz: [T] ms
```

**Aktivieren, wenn:**
- Antwortzeit gleich oder besser UND
- Tokens ≥ 20% weniger UND
- Fehlerrate gleich oder besser

**Deaktivieren, wenn:**
- Antwortzeit > 10% schlechter ODER
- Fehlerrate schlechter

---

## IMPLEMENTIERUNG

**Datei:** `/root/jarvis/command_center/backend/services/voice_to_prompt.py`

**Integration:** In `voice_service.py` nach STT, vor Chat-Aufruf

**Konfiguration (.env):**
```
JARVIS_V2P_ENABLED=false          # Standard: aus, erst nach Messung an
JARVIS_V2P_MODEL=qwen2.5:7b       # LOCAL oder gemini-flash-latest
JARVIS_V2P_TIMEOUT_MS=500
JARVIS_V2P_MIN_WORDS=5
JARVIS_V2P_LOG=true               # Latenz/Token-Messung
```

**Test:** `tests/test_voice_to_prompt.py` (offline, mit Beispiel-Texten)

---

## FAZIT

Das Tool ist ein **Beschleuniger**, kein **Muss**.

- Wenn Paul präzise spricht → Tool bringt wenig
- Wenn Paul natürlich spricht (mit Füllwörtern, Abschweifungen) → Tool spart 20-40% Tokens und reduziert Fehlrouting

**Empfehlung:** 1 Woche messen. Dann entscheiden.

