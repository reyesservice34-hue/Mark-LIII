# MIA BRAIN ARCHITECTURE
## Die Gehirn-Struktur: Wer greift wann auf was zu

**Version:** 1.0  
**Status:** OPTIONAL – Nur umsetzen, wenn Performance-Gewinn messbar  
**Ziel:** Schnellere Antworten durch gezieltes Laden, nicht alles auf einmal  

---

## DAS PROBLEM HEUTE

**Aktuell:** Der Master-Prompt hat ~8.900 Tokens. Bei jeder Anfrage wird ALLES geladen:
- Identität
- Alle 7 Agenten-Definitionen
- Firmenwissen komplett
- Alle Regeln
- Alle Tools

**Folge:**
- Jede Anfrage kostet ~9.000 Input-Tokens (bevor Paul überhaupt etwas gesagt hat)
- Bei Sonnet 5: ~0,027 USD pro Anfrage nur für den Prompt
- Bei 100 Anfragen/Tag: 2,70 USD/Tag = 81 USD/Monat nur für Kontext
- Antwortzeit: 8s (gemessen), davon ~3s nur Prompt-Verarbeitung

**Lösung:** Gehirn in Schichten teilen. Nur laden, was gebraucht wird.

---

## DIE 4 SCHICHTEN

```
┌─────────────────────────────────────────────────────────────┐
│  SCHICHT 0: KERNEL (IMMER geladen, ~800 Tokens)             │
│  ─────────────────────────────────────────────              │
│  • Identität (Name, Stimme, Ton)                            │
│  • Wahrheits-Engine (V/D/A/U)                               │
│  • Autonomie-Level (0-3)                                    │
│  • Grundregel: UNDERSTAND→RETRIEVE→VERIFY→PLAN→EXECUTE      │
│  • Router-Logik: Welcher Intent → welche Schicht laden      │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  SCHICHT 1: AGENT-KONTEXT (bei Bedarf, ~500-1.500 Tokens)   │
│  ─────────────────────────────────────────────              │
│  • NUR der Agent, der gebraucht wird                        │
│  • Seine Rolle, Tools, Regeln, Kontext-Datei                │
│  • Beispiel: Operations → betrieb/kontext.md + Team + Fahrz.│
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  SCHICHT 2: DOMÄNEN-WISSEN (bei Bedarf, ~1.000-3.000 Tokens)│
│  ─────────────────────────────────────────────              │
│  • Firmenwissen (nur relevanter Teil)                       │
│  • Projekt-Daten (nur aktuelles Projekt)                    │
│  • Kunden-Historie (nur aktueller Kunde)                    │
│  • Preise (nur relevante Kategorie)                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  SCHICHT 3: LIVE-DATEN (bei Bedarf, variabel)               │
│  ─────────────────────────────────────────────              │
│  • Kalender (nur abgefragter Zeitraum)                      │
│  • E-Mails (nur relevante)                                  │
│  • Tool-Outputs                                             │
│  • Semantische Suche (memory.search, Top-5)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## ROUTING-TABELLE: WER LÄDT WAS

| Intent | Schicht 0 | Schicht 1 (Agent) | Schicht 2 (Wissen) | Schicht 3 (Live) | Gesamt ~Tokens |
|--------|-----------|-------------------|--------------------|--------------------|----------------|
| „Ja" / „Ok" / „Danke" | ✅ | – | – | – | **800** |
| Status / Übersicht | ✅ | Core | – | Kalender heute, Tasks offen | **2.500** |
| Baustellenplanung | ✅ | Operations | Team, Fahrzeuge, Planungsregeln | Kalender (Datum), Projekt | **4.500** |
| Kalkulation | ✅ | Kalkulation | Preise, Kalkulationsformel | Aufmaß, Projekt | **4.000** |
| Kundenmail | ✅ | Vertrieb | Kommunikationsregeln | Kunde-Historie, letzte Mails | **3.500** |
| Rechnung | ✅ | Finanzen | Rechnungsregeln | Angebot, Projekt | **3.500** |
| Material | ✅ | Einkauf | Lieferanten | memory.search(material) | **3.000** |
| System-Fehler | ✅ | Engineering | Tools, Integrationen | Logs | **3.500** |
| Persönlich | ✅ | Mentor | – | personal/ | **2.500** |
| Merk dir X | ✅ | Core | – | – | **1.000** |

**Vergleich:** Heute ~9.000 Tokens IMMER. Mit Schichten: 800-4.500, im Schnitt ~3.000.

**Ersparnis:** ~65% Input-Tokens. Antwortzeit: geschätzt 8s → 4-5s.

---

## SCHICHT 0: KERNEL – WAS IMMER DA IST

**Datei:** `/data/brain/kernel.md` (~800 Tokens)

**Inhalt (komprimiert):**
```
Du bist JARVIS, Pauls KI für Reyes Service. Deutsch. Ruhig, präzise, direkt, trocken humorvoll. Keine Floskeln, kein „Sir".

WAHRHEIT: Nichts erfinden. Info = VERIFIED/DERIVED/ASSUMPTION[A]/UNKNOWN. Bei UNKNOWN: suchen, dann fragen. Nur erfolgreicher Tool-Aufruf belegt Aktion.

AUTONOMIE: Lesen/Entwürfe/Analysen = automatisch. Versand/Änderungen = Freigabe. Zahlungen/Löschen = immer fragen.

ARBEITEN: Verstehen → Abrufen → Prüfen → Planen → Ausführen → Kontrollieren → Berichten → Merken.

ROUTING: Erkenne Intent. Lade nur den nötigen Agenten (Schicht 1) und das nötige Wissen (Schicht 2). Nicht alles.

AGENTEN: Operations (Baustellen), Kalkulation (Angebote), Vertrieb (Kunden), Finanzen (Rechnungen), Einkauf (Material), Marketing, Engineering (System), Mentor (privat).

NACH AUSSEN: Immer „ich". Agenten unsichtbar.
```

Das ist alles. Der Rest wird nachgeladen.

---

## SCHICHT 1: AGENT-KONTEXT – NUR DER RICHTIGE

**Dateien:** `/data/brain/agents/[agent].md` (je ~500-1.500 Tokens)

**Beispiel `operations.md`:**
```
AGENT: Operations & Projektleitung

ROLLE: Baustellen, Personal, Fahrzeuge, Material, Tagesplanung.

PRÜFEN BEI JEDER BAUSTELLE: Ausgangszustand, Zielzustand, Schutz, Schritte, Personal, Werkzeug, Material, Entsorgung, Abhängigkeiten, Risiken, Doku, Abnahme.

TEAM:
- Paul: Inhaber, Möbel/Türen/Zargen, Karben
- Christoph: Allrounder, KEIN FÜHRERSCHEIN, Reichelsheim → wer nimmt ihn mit?
- Jürgen: Trockenbau/Boden, Steinfurth, kann Christoph mitnehmen
- Bernd: Maler, Steinfurth, kann Christoph mitnehmen

FAHRZEUGE:
- Vivaro: 3 Pers., groß, Material/Entsorgung
- Bipper: 1-2 Pers., klein, Service
- Privat: nur ohne Material

REGELN: 8h-Tag, 1h Buffer, ~7h produktiv. Kalender IMMER zuerst lesen. Material: Paul, Vortag Globus. Kein spontaner Kauf.

TOOLS: calendar.*, filesystem.*, document.create, memory.*, task.*, notify.user
```

**Andere Agenten analog:** `kalkulation.md`, `vertrieb.md`, `finanzen.md`, `einkauf.md`, `marketing.md`, `engineering.md`, `mentor.md`

---

## SCHICHT 2: DOMÄNEN-WISSEN – NUR DER RELEVANTE TEIL

**Dateien:** `/data/brain/knowledge/[domäne].md`

**Aufteilung von `firma.md` in Module:**
```
/data/brain/knowledge/
├─ firma_basis.md        (Name, Leistungen, Adressen – ~200 Tokens)
├─ team.md               (Personen, Stärken, Wohnorte – ~300 Tokens)
├─ fahrzeuge.md          (Vivaro, Bipper, Regeln – ~200 Tokens)
├─ planung.md            (Arbeitszeit, Material, Werkzeug – ~600 Tokens)
├─ werkzeug.md           (Checklisten nach Gewerk – ~500 Tokens)
├─ kalkulation.md        (Formel, Regeln, Stundensätze – ~400 Tokens)
├─ kommunikation.md      (Ton, Verbote, Marke – ~300 Tokens)
├─ qualitaet.md          (Baustellenregeln, Prüfung – ~300 Tokens)
└─ betriebsprinzip.md    (Jarvis-Regeln, Ich-Form, Freigaben – ~400 Tokens)
```

**Routing:** Operations lädt `team.md` + `fahrzeuge.md` + `planung.md`. Kalkulation lädt `kalkulation.md` + `werkzeug.md`. Vertrieb lädt `kommunikation.md`. Nicht alle laden alles.

---

## SCHICHT 3: LIVE-DATEN – NUR WAS GEFRAGT IST

**Quellen:**
- `calendar.read(von, bis)` – nur der abgefragte Zeitraum, nicht der ganze Monat
- `memory.search(query, top_k=5)` – semantische Suche, nur Top-5
- `email.search(kunde, limit=3)` – nur die letzten 3
- `filesystem.read(/projects/[projekt]/)` – nur das aktuelle Projekt
- Tool-Outputs – direkt, ungefiltert

**Regel:** Schicht 3 wird NIE vorab geladen. Nur wenn der Agent sie anfordert.

---

## CACHING – NICHT ZWEIMAL LADEN

**Was gecacht wird (Working Memory, pro Session):**
- Schicht 0 (Kernel) – immer im Cache
- Schicht 1 (letzter Agent) – bleibt, bis Agent wechselt
- Schicht 2 (letztes Wissen) – bleibt, bis Domäne wechselt
- Kalender-Reads – 15 Min gültig
- Kunden-Historie – 1 Std gültig
- Preise – 24 Std gültig

**Cache-Invalidierung:**
- Kalender geändert → Kalender-Cache leeren
- Neuer Kunde erwähnt → Kunden-Cache leeren
- Paul sagt „Merk dir X" → memory neu indexieren

**Prompt-Caching (OpenRouter/Anthropic):**
- Schicht 0 + Schicht 1 als gecachten Prefix senden
- Nur Schicht 2 + 3 + Pauls Nachricht sind neu
- Ersparnis: ~90% auf gecachte Tokens

---

## DER ROUTER – WER ENTSCHEIDET WAS GELADEN WIRD

**Position:** Im Mia Core, vor dem Agenten-Aufruf

**Logik:**
```python
def route(user_input, working_memory):
    # 1. Bypass für Kurzantworten
    if len(user_input.split()) < 5 and is_confirmation(user_input):
        return load_layers([0])
    
    # 2. Intent erkennen (LOCAL/CHEAP Modell oder Voice-to-Prompt-Tool)
    intent = classify_intent(user_input)
    
    # 3. Agent bestimmen
    agent = INTENT_TO_AGENT[intent]
    
    # 4. Wissen bestimmen
    knowledge = AGENT_TO_KNOWLEDGE[agent]
    
    # 5. Live-Daten bestimmen (nur Anweisung, nicht laden)
    live_needs = INTENT_TO_LIVE[intent]
    
    # 6. Cache prüfen
    if working_memory.agent == agent:
        skip_layer_1 = True
    if working_memory.knowledge == knowledge:
        skip_layer_2 = True
    
    # 7. Laden
    return load_layers([0, 1 if not skip_layer_1, 2 if not skip_layer_2], live_needs)
```

**Modell für Intent-Klassifikation:** LOCAL (qwen2.5:7b, ~200ms) oder CHEAP (Gemini Flash, ~300ms). NICHT Sonnet.

---

## MIGRATION: VON HEUTE ZU SCHICHTEN

**Schritt 1:** Kernel extrahieren (~800 Tokens) aus dem aktuellen Master-Prompt
**Schritt 2:** Agenten-Dateien anlegen (8 Dateien, je ~500-1.500 Tokens)
**Schritt 3:** `firma.md` in 9 Wissens-Module splitten
**Schritt 4:** Router implementieren (`backend/orchestrator/brain_router.py`)
**Schritt 5:** Caching einbauen (Working Memory + Prompt-Cache)
**Schritt 6:** 1 Woche parallel messen (alt vs. neu)
**Schritt 7:** Umschalten, wenn neu besser

**Konfiguration (.env):**
```
JARVIS_BRAIN_LAYERED=false        # Standard: aus
JARVIS_BRAIN_PATH=/data/brain
JARVIS_BRAIN_ROUTER_MODEL=qwen2.5:7b
JARVIS_BRAIN_CACHE_TTL_CALENDAR=900
JARVIS_BRAIN_CACHE_TTL_CLIENT=3600
JARVIS_BRAIN_LOG=true
```

**Rückweg:** `JARVIS_BRAIN_LAYERED=false` → alter Master-Prompt

---

## MESSUNG: LOHNT SICH DAS?

**Vor Umschaltung 1 Woche messen:**
```
ALT (Monolith):
  - Input-Tokens/Anfrage: ~9.000
  - Antwortzeit: ~8s
  - Kosten/Tag (100 Anfragen): ~2,70 USD
  - Fehlerrate (falscher Kontext): [messen]

NEU (Schichten):
  - Input-Tokens/Anfrage: ~3.000 (Schnitt)
  - Antwortzeit: ~4-5s (Ziel)
  - Kosten/Tag: ~0,90 USD
  - Fehlerrate: [messen – darf nicht steigen]
  - Router-Latenz: ~200-300ms
```

**Umschalten, wenn:**
- Tokens ≥ 40% weniger UND
- Antwortzeit ≤ gleich UND
- Fehlerrate ≤ gleich

**Nicht umschalten, wenn:**
- Fehlerrate steigt (Mia vergisst Kontext, den sie früher hatte)
- Antwortzeit steigt (Router zu langsam)

---

## RISIKEN

**Risiko 1: Router lädt falschen Agenten**
→ Fallback: Bei Unsicherheit (Confidence < 0.7) → 2 Agenten laden

**Risiko 2: Wissen fehlt, das früher immer da war**
→ Fallback: Agent kann jederzeit `filesystem.read(/data/brain/knowledge/*.md)` nachladen

**Risiko 3: Cache veraltet**
→ TTLs kurz halten, Invalidierung bei Änderungen

**Risiko 4: Komplexität steigt**
→ Klare Dateistruktur, Logging, Rückweg per Flag

---

## FAZIT

**Das Schichten-Gehirn ist ein Performance-Upgrade, kein Muss.**

- Heute: Alles laden, 9.000 Tokens, 8s
- Mit Schichten: Gezielt laden, ~3.000 Tokens, ~4-5s, 65% günstiger

**Empfehlung:** Erst messen. Dann umschalten. Rückweg per Flag.

**Und:** Beide Dokumente (KERNEL + CONSOLIDATED_MASTER) bleiben die Quelle der Wahrheit. Die Schichten sind nur eine andere Art, sie zu laden.

