# JARVIS HAUPTGEDÄCHTNIS

> **Stand:** 19.09.2026 · **Angelegt von:** Claude Code
>
> **Wichtiger Hinweis zur Quellenlage.** Diese Datei sollte aus acht benannten
> Quelldateien unter `.claude/` zusammengeführt werden. **Keine dieser Dateien
> existiert auf diesem System** — weder im Repository, noch in dessen
> Git-Historie, noch sonst auf der Platte (Suchprotokoll siehe Abschnitt 19).
> `.claude/` gab es vor dieser Datei nicht.
>
> Deshalb enthält dieses Hauptgedächtnis **ausschließlich Belegtes**: Fakten aus
> dem Quelltext dieses Repositoriums, aus der Konfiguration und aus Prüfungen am
> laufenden Server. **Nichts ist aus den fehlenden Dateien erraten.** Jeder Punkt
> nennt seine Quelle. Was noch aus den Quelldateien kommen muss, steht als
> `QUELLE FEHLT`.
>
> Es wurde keine Datei gelöscht, überschrieben oder verschoben.

---

## 1. Identität und Zweck

- **Name:** JARVIS. *(Quelle: `core/prompt.txt`, `CLAUDE.md`)*
- **Zweck:** Ein Assistent mit zwei Hälften und **einem** Gehirn. Der Server
  denkt, entscheidet und merkt sich alles; der PC ist Hand und Auge und führt
  aus, was nur vor Ort geht. *(Quelle: `command_center/STRUKTUR.md`)*
- **Ein Gehirn, nicht zwei.** Früher antwortete die Desktop-App mit eigener
  Sitzung und eigener Persona — deshalb widersprachen sich JARVIS auf WhatsApp
  und JARVIS am Rechner. Sobald ein Gateway-Token gesetzt ist, hört der Desktop
  auf, ein zweites Gehirn zu sein, und wird **Client** des einen serverseitigen
  JARVIS. *(Quelle: `readme.md`, `core/control_plane.py`)*
- **Anrede an den Nutzer:** eine gespeicherte Einstellung, Voreinstellung
  `mein Herr`. Werkzeugtexte tragen selbst keine Anrede. *(Quelle: Persona-Einstellung)*
- **Antwortsprache:** Deutsch, solange der Nutzer deutsch schreibt.
  *(Quelle: `CLAUDE.md`)*

## 2. Nutzer und Präferenzen

- **Nutzer:** Yamil Reyes, `reyesservice34@gmail.com`. *(Quelle: Git-Konfiguration, Sitzung)*
- **Betrieb:** Reyes Service — Handwerk und Innenausbau. *(Quelle: Domain `jarvis-reyes.de`, n8n-Instanz `reyesservice.app.n8n.cloud`)*
- **Sprache:** Deutsch.
- **Arbeitsweise, die er ausdrücklich verlangt** *(Quelle: `CLAUDE.md`)*:
  - Eigenständig mitdenken; kleine Entscheidungen selbst treffen statt rückzufragen.
  - Prüfen, ob es etwas schon gibt, bevor etwas neu gebaut wird.
  - Lücken benennen und schließen, nicht dekorieren. Kein Platzhalter, der so
    aussieht, als könne er etwas.
  - Nur fragen, wenn ein Fehler teuer oder unumkehrbar ist.
  - Nie einen Plan ankündigen statt zu arbeiten.
- **Bevorzugte Form der Anleitung** *(Quelle: Verlauf dieser Zusammenarbeit)*:
  ein Block zum Kopieren, nicht sechs Schritte von Hand. Kurze Antworten.

## 3. Systemarchitektur

```
Browser ──HTTPS──▶ Reverse Proxy ──▶ FastAPI-Backend (Port 8080, nur lokal gebunden)
                                      ├─ Auth: Sitzungen, CSRF, Rollen, Maschinen-Token
                                      ├─ Event-Bus → Server-Sent Events
                                      ├─ Master Agent → Anthropic · OpenAI · Gemini · lokal
                                      │    └─ Werkzeugverzeichnis → Freigabe-Gatter → Prüfspur
                                      └─ SQLite (WAL) + abgeschotteter Arbeitsbereich
PC (desktop_agent.py) ──X-Jarvis-Token──▶ /v1/desktop/*   (Long-Poll nach außen)
```

- **Backend:** FastAPI + SQLite im WAL-Modus, 29 Tabellen. *(Quelle: `command_center/backend/db.py`)*
- **Frontend:** Vite + React + TypeScript, als statische Dateien vom Backend ausgeliefert.
- **Module** registrieren sich selbst über `ModuleSpec`; Werkzeuge über `ToolSpec`
  mit Risikostufe, Mindestrolle und Freigabepflicht. *(Quelle: `orchestrator/tool_registry.py`)*
- **Rollen:** `viewer` < `operator` < `admin`.
- **Freigabe-Gatter:** Ein Aufruf hoher Risikostufe **blockiert wirklich** in der
  Denkschleife; der Lauf meldet `WAITING_FOR_APPROVAL`. Kein Dialog, sondern ein Tor.
- **Richtung der Verbindung zum PC:** Der Server kann keinen Rechner hinter einem
  Heimrouter anwählen. Also hält der PC eine Verbindung nach außen offen und holt
  sich Aufträge. **Am PC lauscht kein Port.** *(Quelle: `services/desktop_bridge.py`)*
- **Wichtig zur Abgrenzung:** Befehle laufen über den Long-Poll
  (`/v1/desktop/poll`, `/v1/desktop/result`), **nicht** über WebSocket. Der
  WebSocket (`/api/voice/live`, WSS) ist ausschließlich die Tonleitung.
  `JARVIS_WS_URL` wird **nicht** verwendet.

## 4. Server und Infrastruktur

| Was | Wert | Quelle |
|---|---|---|
| Öffentliche Adresse | `https://jarvis.jarvis-reyes.de` | geprüft |
| Betriebssystem | Ubuntu, Anmeldung als `root@ubuntu` | geprüft |
| **Pfad des Quelltexts** | **`/root/jarvis`** — *nicht* `/root/Mark-LIII` | geprüft |
| Compose-Datei | `docker-compose.command-center.yml` — *nicht* `docker-compose.yml` | geprüft |
| Container | `jarvis-command-center` | geprüft |
| Dienstname in Compose | `jarvis-command-center` — *nicht* `command_center` | geprüft |
| Portbindung | `127.0.0.1:8080` (nur lokal, davor ein Proxy) | geprüft |
| Konfiguration | `/root/jarvis/command_center/.env` (gitignored) | geprüft |
| Datenablage | Docker-Volume `jarvis-cc-data` → `/data` | `docker-compose.command-center.yml` |

**Befehle, die auf diesem Server wirklich funktionieren** *(alle vier Abweichungen
haben bereits je einmal Zeit gekostet)*:

```bash
cd /root/jarvis
docker compose -f docker-compose.command-center.yml ps
docker compose -f docker-compose.command-center.yml logs jarvis-command-center
bash command_center/install.sh          # holt, baut, erzeugt Container NEU
```

**`docker compose restart` lädt eine geänderte `.env` NICHT.** Ein Container
bekommt seine Umgebung beim *Erzeugen*. Ein neu eingetragener Schlüssel bleibt
nach einem Neustart unsichtbar, während die `.env` richtig aussieht — eine
Stunde Suche an der falschen Stelle. Richtig ist `up -d --force-recreate`, und
genau das tut `install.sh`. *(Erkenntnis vom 18.09.2026)*

## 5. Brain / Graphify

**QUELLE FEHLT.** Im Quelltext dieses Repositoriums gibt es **kein** System
namens *Graphify* und keinen Wissensgraphen. Ein Verzeichnis `knowledge_graphs/`
existiert nicht.

Was es gibt, ist der Begriff **„brain"** als Bezeichnung für den Master Agent in
der Selbstauskunft: `inventory.py` liefert unter dem Schlüssel `brain` den
aktiven Modus und das Modell. *(Quelle: `command_center/backend/services/inventory.py:55`)*
Das ist ein Anzeigename, kein eigenes Teilsystem.

→ Falls es Brain/Graphify tatsächlich gibt, steht es in den fehlenden
Quelldateien und muss hier ergänzt werden.

## 6. Memory

Das Gedächtnis hat **drei** Ebenen, die sich in Form und Zweck unterscheiden
*(Quelle: `command_center/backend/modules/memory/__init__.py`)*:

| Ebene | Form | Inhalt | Grenze |
|---|---|---|---|
| **Stehende Anweisungen** | ein Textfeld | **Regeln**: „Antworte kurz" | 20 000 Zeichen |
| **Hauptgedächtnis** (angeheftet) | Liste einzelner Sätze | **Tatsachen**, die bei *jeder* Anfrage mitgehen | **30 Sätze** |
| **Gemerktes** | Liste | Tatsachen, die bei Bedarf gesucht werden | — |

- **Warum zwei Formen:** Ein falscher Merksatz vergiftet still jedes weitere
  Gespräch. Den muss man einzeln herausziehen können, ohne den Rest anzufassen.
  Eine Regel dagegen liest sich als Ganzes besser.
- **Warum 30:** Keine Schikane, sondern Physik. Was angeheftet ist, wird bei
  jeder Anfrage mitgeschickt und jedes Mal bezahlt. Dreißig gute Sätze wirken,
  dreihundert ertränken sie. *(Konstante `MAX_PINNED = 30`)*
- Beides wirkt **sofort**, ohne Neustart, und gilt für Chat **und** Sprachleitung
  gleichermaßen.
- **Desktop-Gedächtnis** getrennt davon: `memory/memory_manager.py` auf dem PC.

## 7. Ollama / KI

**Aktive Anbieter** *(geprüft am 18.09.2026 am laufenden Server)*:

| Anbieter | Zustand | Modell |
|---|---|---|
| Anthropic | läuft | `claude-opus-5` |
| OpenAI | läuft | 130 Modelle verfügbar |
| Google Gemini | läuft | `gemini-flash-latest` |
| **Ollama / LM Studio** | **nicht verbunden** | `LOCAL_LLM_URL` nicht gesetzt |

- **Ollama ist vorgesehen, aber nicht eingerichtet.** Der Anschluss ist gebaut:
  `LOCAL_LLM_URL` + `LOCAL_LLM_MODEL`, angesprochen über die
  OpenAI-kompatible Schnittstelle. *(Quelle: `backend/ai/openai_compat.py`, `.env.example:39`)*
- **Wie er anzuschließen wäre:** `http://ollama:11434`, wenn Ollama als Container
  im selben Netz läuft — oder `http://host.docker.internal:11434`, wenn Ollama
  direkt auf dem Host lauscht. **Nicht `127.0.0.1`**: im Container ist das der
  Container selbst. Dafür ist `extra_hosts: host.docker.internal:host-gateway`
  in der Compose-Datei eingetragen.
- **Anbieterwahl:** `JARVIS_AI_PROVIDER` leer → der erste konfigurierte.
  `JARVIS_MASTER_AGENT_MODE=auto` → lokal, wenn ein Schlüssel da ist.

→ **QUELLE FEHLT:** Welches Ollama-Modell gewünscht ist, steht nicht im Repo.

## 8. Voice

**Sprachausgabe — Rangfolge** *(Quelle: `backend/services/voice_service.py`)*:

1. **ElevenLabs**, wenn `ELEVENLABS_API_KEY` **und** `ELEVENLABS_VOICE_ID`
   gesetzt sind. Standardmodell `eleven_multilingual_v2` (spricht Deutsch).
   Abruf als PCM 24 000 Hz, serverseitig in WAV verpackt.
2. Ein beliebiger OpenAI-kompatibler TTS-Endpunkt (`JARVIS_CC_TTS_URL`).
3. Ohne beides: abgeschaltet, mit Nennung der fehlenden Variable.

**Grundsatz:** Der Schlüssel bleibt auf dem Server. Browser und PC bekommen
**fertiges Audio**, nie den Schlüssel. Auf dem PC spielt `actions/speak_audio.py`
nur ab, was ihm gereicht wird — die Datei enthält weder Schlüssel noch Adresse.

**Offene Leitung** (`/api/voice/live`, WSS):
- OpenAI Realtime über den Server weitergereicht, PCM16 bei 24 kHz,
  `semantic_vad` für Unterbrechen mitten im Satz.
- **Die Leitung überlebt den Seitenwechsel im Dashboard** — ausdrücklich
  gewünscht und durch einen Browsertest abgesichert. Sie schließt erst auf
  Ansage. *(Umgesetzt in `frontend/src/app/voice/liveStore.ts`)*
- Stimmen mit Tiefe, die zur Rolle passen: `cedar`, `marin`, `alloy`, `echo`,
  `shimmer` (`JARVIS_CC_REALTIME_VOICE`).
- Die Persona für die offene Leitung verbietet Markdown im gesprochenen Wort und
  untersagt, unbestätigte Arbeit als erledigt auszugeben.

**Spracherkennung:** `JARVIS_CC_STT_URL`, jeder OpenAI-kompatible Endpunkt
(faster-whisper-server, whisper.cpp, Speaches, LocalAI). Kein Audio wird gespeichert.

→ **QUELLE FEHLT:** Die gewünschte Stimmen-Kennung (`ELEVENLABS_VOICE_ID`) steht
nirgends im Repo — sie gehört in die `.env` auf dem Server.

## 9. Agenten

- **Master Agent** — die Denkschleife: Werkzeuge, Delegation, Freigabe-Gatter,
  Aufgaben. Drei Betriebsarten: `local` (eigener Schlüssel), `remote` (Weiterleitung
  an eine vorgelagerte Steuerebene), `none` (dann sagt der Chat genau das).
- **Spezialisten** entstehen aus **Vorführungen** (Lernmodus): Aufnahme →
  Prozedur → Spezialist mit eigenen Anweisungen, begrenzt auf die Werkzeuge der
  Prozedur, dauerhaft gespeichert (`learned_agents`). *(Quelle: `services/teaching.py`)*
- **Zwei Ehrlichkeitsregeln beim Lernen:** Ein Schritt darf nur ein Werkzeug
  nennen, das **existiert** (erfundene werden verworfen); ein echtes Werkzeug,
  dem nur Zugangsdaten fehlen, bleibt erhalten — mit dem Vermerk, dass es noch
  eingerichtet werden muss.
- **Ein gelernter Agent steht in zwei Tabellen:** `agents` und `learned_agents`.
  Nur eine zu leeren heißt: er ist weg bis zum nächsten Neustart. *(Erkenntnis 19.09.2026)*
- **Der Master ist nicht löschbar und nicht abschaltbar.** Ohne ihn antwortet
  nichts mehr.
- **Agentur auf dem Desktop** (`core/agency.py`): Der Flussgraph **ist** das
  Rechtemodell — ein Agent delegiert nur entlang eines Paars, in dem er vorkommt.
  Das Budget gilt für den ganzen Lauf, nicht je Agent.

## 10. Automationen / n8n

**Eigene Hintergrundaufträge** *(Quelle: `modules/automations/`)*:
`metrics_sample`, `metrics_persist`, `docker_probe`, `integration_health`,
`master_health`, `approval_expiry`, `session_purge`, `log_trim`,
`workflow_sync`, `self_reflection` (einmal täglich: aus der Prüfspur des
letzten Tages **einen** Verbesserungsvorschlag ableiten — und ihn zur Freigabe
hinlegen, nicht selbst umsetzen).

Dazu Aufträge aus gelernten Prozeduren mit Zeitplan, erkennbar am Präfix
`procedure:`. Diese sind löschbar — wobei das Löschen auf den **Zeitplan der
Prozedur** wirkt, nicht auf den Auftrag allein; der käme sonst beim nächsten
Abgleich zurück. Die Aufträge des Betriebs bleiben; abschalten geht.

**n8n** *(Stand 19.09.2026)*:

| | Wert |
|---|---|
| Genutzte Instanz | **n8n Cloud: `https://reyesservice.app.n8n.cloud`** |
| Eingebunden als | MCP-Server (`/mcp-server/http`) — antwortet mit HTTP 200 |
| `N8N_BASE_URL` zeigte auf | `http://n8n:5678` — ein **anderer** Container, antwortet 401 |

**⚠️ Der zentrale Befund:** Ein n8n-API-Schlüssel ist ein JWT und gilt **nur bei
der Instanz, die ihn ausgestellt hat**. Ein Cloud-Schlüssel, an einen Container
nebenan geschickt, ergibt genau dieses 401 — das aussieht wie „abgelaufen", aber
eine falsche Adresse ist. `N8N_BASE_URL` muss auf die Cloud-Instanz zeigen.
*(Erkenntnis 19.09.2026; die Diagnose steht seither im Dashboard selbst)*

`N8N_WEBHOOK_BASE_URL` wird nur gebraucht, wenn n8n seine Webhooks unter einer
anderen Adresse ausliefert als seine API. Sonst fällt es auf `N8N_BASE_URL` zurück.

## 11. Qdrant / Suche

**QUELLE FEHLT.** **Qdrant kommt im Quelltext dieses Repositoriums nirgends vor.**
Es gibt keine Vektordatenbank, keine Einbettungen und keine semantische Suche.

Was es an Suche tatsächlich gibt:
- `web.search` / `web.fetch` — DuckDuckGo über HTML, **ohne Schlüssel und ohne Konto**.
- `filesystem.search` — Dateien im Arbeitsbereich nach Namen.
- `conversation.search` — in früheren Gesprächen nachsehen.
- `logs.search` — in der Prüfspur.

→ Falls Qdrant geplant oder anderswo im Einsatz ist, steht das in den fehlenden
Quelldateien.

## 12. Kommunikation / WhatsApp

- **WhatsApp läuft über den gekoppelten PC**, nicht über die Business-API von
  Meta. Der Kanal ist die einmal per QR verknüpfte WhatsApp-Web-Sitzung.
  *(Quelle: `plugins/whatsapp_voice.py`, `.env.example`)*
- **Deshalb braucht der Server dafür keine Zugangsdaten.** Kein Token, kein Konto.
- Im Dashboard steht WhatsApp genau dann als verfügbar, wenn ein gekoppelter PC
  diese Fähigkeit meldet **und** erreichbar ist — nicht, weil eine Variable gesetzt ist.
- **Sprachnachrichten in der eigenen Stimme:** Der freie Ersatzweg kann die
  Stimme nicht nachahmen; wenn er einspringt, **wird das in der Antwort gesagt**.
  Eine misslungene Aufnahme erreicht den Nutzer trotzdem — als Text.
- **⚠️ Ungeprüft:** Die DOM-Schritte gegen WhatsApp Web sind **nicht** gegen die
  Live-Seite verifiziert. Erst mit `dry_run` arbeiten.

**E-Mail:** IMAP lesen, SMTP senden; Server werden aus der Domain der Adresse
erraten (Gmail, Outlook, IONOS, Strato, GMX, web.de, Posteo, mailbox.org, Yahoo).
**Senden braucht immer eine Freigabe.** Gmail und Outlook brauchen ein
App-Passwort, nicht das Kontopasswort.

## 13. Dashboard und Interfaces

- **Adresse:** `https://jarvis.jarvis-reyes.de`
- **Anmeldung:** Sitzungs-Cookie (HttpOnly) + CSRF-Token; Rollen wie in Abschnitt 3.
- **Aufbau:** dunkles Kommandozentralen-Design, Befehlspalette (⌘K), Statusleiste,
  nachladende Routen, wiederverbindende Ereignisleitung mit Nachlieferung über
  `Last-Event-ID`. Bis 390 px Breite bedienbar.
- **Seiten:** Chat, Agenten, Aufgaben, Workflows, Automatisierungen, Geräte,
  Erweiterungen, Integrationen, Server, Dateien, Logbuch, Auswertung,
  Meldungen, Freigaben, Lernen, Gedächtnis, Einstellungen.
- **Überall anlegen, ändern und löschen** — seit 19.09.2026 lückenlos. Zwei
  Ausnahmen mit Absicht: der **Master-Agent** und die **Aufträge des Betriebs**
  bleiben. Bei ihnen fehlt der Knopf ganz, statt eine Absage zu zeigen, die
  niemand vorher erraten konnte; abschalten geht und ist umkehrbar.
- **Schnittstellen:**
  - `/api/*` — das Dashboard, hinter Sitzung + CSRF
  - `/v1/commands` — für Skript, Handy, n8n (Maschinen-Token)
  - `/v1/desktop/*` — der gekoppelte PC (Long-Poll)
  - `/api/voice/live` — die offene Tonleitung (WSS)
  - `/api/events/stream` — Server-Sent Events

## 14. Geräte und Clients

- **Ein Gerät trägt sich nicht in eine Liste ein.** Es meldet sich selbst, sobald
  auf ihm etwas mit gültigem Maschinen-Token läuft.
- **Maschinen-Token:** `jcc_` + 36 Byte Zufall, gespeichert wird **nur der
  SHA-256-Hash**. Gesendet im Kopf `X-Jarvis-Token`. Einmal gezeigt, nie wieder.
- **Ankoppeln** (seit 19.09.2026): Dashboard → Geräte → „Rechner hinzufügen".
  Der Server erzeugt Token **und** Installationsskript; der Nutzer kopiert **einen
  Befehl**. Das Skript prüft Python, lädt den Quelltext als ZIP (kein git nötig),
  installiert die Abhängigkeiten, trägt das Token ein und prüft die Verbindung.
- **Was das Skript bewusst nicht tut:** Python selbst installieren — das verändert
  den PATH eines fremden Rechners. Eine vorhandene Installation wird **beiseite
  gelegt**, nicht überschrieben.
- **Gemeldete Fähigkeiten je Gerät:** Mikrofon, Lautsprecher, Tastatur und Maus,
  Bildschirm, Sprachausgabe, Browser — **gemessen, nicht behauptet**. Der Agent
  entscheidet danach: „ich sage es dir laut" auf einem Rechner ohne Lautsprecher
  wäre eine Zusage, die niemand hört.
- **Autostart unter Windows:** Aufgabenplanung
  (`schtasks /sc onlogon /rl limited`, Aufgabe „JARVIS Desktop Bridge"), mit
  Rückfall auf den Autostart-Ordner. **Kein Dienst:** ein Dienst liefe ohne
  angemeldeten Benutzer und könnte weder tippen noch klicken noch den Bildschirm
  sehen — genau das, wofür die Kopplung da ist.
- **Nie fernsteuerbar:** `dev_agent`, `agency_agent`, `shutdown_jarvis`.
- **Geplanter Gerätename:** `YAM-DESKTOP` *(`JARVIS_DEVICE_NAME`)*.

## 15. Wichtige Entscheidungen

| Entscheidung | Warum |
|---|---|
| **Ein Gehirn auf dem Server**, der PC ist Client | Zwei Gehirne widersprachen sich — WhatsApp sagte etwas anderes als der Rechner |
| **Verbindung immer vom PC nach außen** | Kein offener Port am Heimrechner, kein VPN, keine Portweiterleitung |
| **Geheimnisse ausschließlich auf dem Server** | Nie in den Browser, nie ins Bundle, nie ins Git |
| **Composio zuerst, aber nie blockierend** | Ein Schlüssel statt einem Dutzend — aber sein Fehlen ist nie ein Grund, die Arbeit zu stoppen |
| **Werkzeuge mit Freigabe statt freier Shell** | Eine unbegrenzte Fernsteuerung wäre nicht abzusichern; stattdessen eine Positivliste |
| **Drei Composio-Werkzeuge statt tausend** | Tausende Deklarationen kaufen Verwirrung, keine Fähigkeit: erst entdecken, dann handeln |
| **Keine kostenpflichtigen Dienste als Voreinstellung** | Gemini-Freikontingent, lokales Modell, EdgeTTS, WhatsApp Web, DuckDuckGo |
| **Am eigenen Quelltext nur über Git** | Jede Änderung ein Commit mit Begründung — nachlesbar und mit `revert` umkehrbar |
| **Schreiben am Quelltext ist `critical`** | Wer ihn ändert, ändert, was der Server als Nächstes tut — Freigabe und Administratorrolle |
| **Der Master-Agent ist unlöschbar** | Ohne ihn antwortet nichts mehr — das wäre kein Löschen, sondern ein Abschalten über einen Umweg |

## 16. Laufende Projekte

- **Arbeitszweig:** `claude/agency-agent-installation-ransfo`,
  Pull Request [#1](https://github.com/reyesservice34-hue/Mark-LIII/pull/1) (Entwurf).
- **Repository:** `https://github.com/reyesservice34-hue/Mark-LIII` — **öffentlich**.
- **Selbstreparatur** (`source.*`, seit 19.09.2026): JARVIS kann seinen eigenen
  Quelltext lesen, ändern und löschen — über Git, mit Freigabe, gesperrt für
  `.env`, `.git/`, `data/`. **Standardmäßig aus**; einzuschalten mit
  `JARVIS_CC_SOURCE_DIR=/repo` + `JARVIS_CC_SOURCE_MOUNT=/root/jarvis`.
- **Offen aus früheren Gesprächen:**
  - Restliche Seiten ins Deutsche übersetzen (Einstellungen, Agenten, Aufgaben,
    Server, Dateien, Integrationen, Logbuch, Auswertung, Meldungen, Freigaben,
    Workflows, Automatisierungen, Lernen).
  - **Admin-Passwort wechseln** — das bisherige stand im Chat, und die Seite ist
    öffentlich erreichbar.
  - **In den Chat eingefügte Schlüssel widerrufen** (Anthropic, OpenAI, Composio,
    n8n, ein GitHub-Token, ein Maschinen-Token). Keiner davon wurde je benutzt.

## 17. Langfristige Erkenntnisse

**Technische Erkenntnisse, die je einmal Zeit gekostet haben:**

1. **Ein einziges fremdes Werkzeugschema kann jede Antwort verhindern.** Anthropic
   lehnt `oneOf`/`allOf`/`anyOf` auf oberster Ebene ab — und damit die *ganze*
   Anfrage, samt der fünfzig anderen Werkzeuge. Schemata von MCP-Servern werden
   deshalb am Übergang zum Anbieter zurechtgelegt, nicht durchgereicht.
2. **Punkte in Werkzeugnamen** weist jeder Anbieter ab. `ToolNameMap` schreibt sie
   an der Leitung um und zurück.
3. **Ein Container bekommt seine Umgebung beim Erzeugen.** `restart` lädt eine
   geänderte `.env` nicht — nur `up -d --force-recreate`.
4. **`127.0.0.1` im Container ist der Container selbst.** Ein n8n, das auf dem Host
   unter `127.0.0.1:5678` liegt, ist von dort aus unerreichbar.
5. **Ein n8n-Schlüssel gilt nur bei seiner eigenen Instanz.** Ein 401 heißt nicht
   von selbst „abgelaufen" — das Ablaufdatum steht offen im JWT und widerlegt es.
6. **Starlette meldet einen WebSocket, der vor `accept()` geschlossen wird, als
   HTTP 403** und verbirgt damit den wahren Grund.
7. **Was der Server aus dem Repo importiert, muss auch ins Abbild.** Der lokale
   Kalender stand auf „offline", weil das Dockerfile `plugins/_calendar_core.py`
   nicht kopierte — während alle Tests grün waren, denn die laufen im Repo.
8. **Docker-Compose braucht `--env-file`**, damit Platzhalter in der Compose-Datei
   ersetzt werden.
9. **Inline-Kommentare in einer `.env` werden mitgelesen.** `KEY=wert # Hinweis`
   ergibt einen Wert samt Hinweis.
10. **Deutsche typografische Anführungszeichen in f-Strings und JSX-Attributen**
    zerbrechen die Datei — zweimal passiert.

**Grundsätze, die sich bewährt haben:**
- Ein Werkzeug ohne Zugangsdaten **nennt die fehlende Variable**, statt zu scheitern.
- Ein Zustand wird erst grün gemeldet, wenn eine **echte Anfrage** gelungen ist.
- Eine Fehlermeldung, die den nächsten Schritt nicht nennt, ist unfertig.

## 18. Offene technische Punkte

| Punkt | Stand |
|---|---|
| **`N8N_BASE_URL` auf die Cloud-Instanz umstellen** | vom Nutzer einzutragen |
| **`COMPOSIO_USER_ID`: `defauult` → `default`** | Tippfehler in der `.env`, aus den Logs belegt |
| **Composio-Schlüssel wird abgewiesen** | neuer Schlüssel nötig |
| **GitHub-Token** | war eingetragen, wurde aber nie geladen (siehe Erkenntnis 3) |
| **ElevenLabs** | `ELEVENLABS_API_KEY` und `ELEVENLABS_VOICE_ID` fehlen noch |
| **Ollama** | nicht eingerichtet; Anschluss vorhanden |
| **Qdrant / Graphify** | im Quelltext nicht vorhanden — Zweck ungeklärt |
| **WhatsApp-DOM-Schritte** | ungeprüft gegen die Live-Seite |
| **Admin-Passwort** | steht im Chatverlauf, Seite ist öffentlich |

---

## 19. Protokoll dieser Zusammenführung

*(Abschnitt 7 der Anforderung: die Punkte gehören ins Hauptgedächtnis selbst.)*

### 19.1 Analysierte Quelldateien

| Angeforderte Quelle | Ergebnis |
|---|---|
| `.claude/jarvis_master_system.md` | **nicht gefunden** |
| `.claude/jarvis_memory_system.json` | **nicht gefunden** |
| `.claude/jarvis_voice_memory.md` | **nicht gefunden** |
| `.claude/long_term_memory.md` | **nicht gefunden** |
| `.claude/memory_log.json` | **nicht gefunden** |
| `.claude/session_learning.json` | **nicht gefunden** |
| `.claude/whatsapp_configuration_memory.md` | **nicht gefunden** |
| `.claude/knowledge_graphs/` | **nicht gefunden** |

**Suchprotokoll:** `.claude/` existierte im Arbeitsverzeichnis nicht; `git log --all -- .claude`
ist leer; eine Suche über die gesamte Platte nach allen acht Namen blieb ohne
Treffer. Das einzige gefundene `.claude` liegt unter `/root/.claude` und ist das
Arbeitsverzeichnis des Werkzeugs *Claude Code* (Hooks, Sitzungen, Skills) — nicht
JARVIS' Gedächtnis.

**Tatsächlich ausgewertete Quellen:**

| Quelle | Woraus |
|---|---|
| `CLAUDE.md` | Arbeitsweise, Sprache, unverhandelbare Regeln |
| `command_center/STRUKTUR.md` | Architektur, Verzeichnisse, Module |
| `command_center/.env.example` | Dienste, Variablen, Voreinstellungen |
| `command_center/backend/modules/memory/__init__.py` | Gedächtnisstruktur, `MAX_PINNED = 30` |
| `command_center/backend/services/voice_service.py` | ElevenLabs-Rangfolge |
| `command_center/backend/services/teaching.py` | Lernmodus, Spezialisten |
| `command_center/backend/services/inventory.py` | Bedeutung von „brain" |
| `command_center/backend/adapters/integrations.py` | Konnektoren, n8n-Diagnose |
| `docker-compose.command-center.yml` | Container, Volumes, Netz |
| `readme.md` | „ein Gehirn statt zwei" |
| Prüfungen am laufenden Server (18./19.09.2026) | Zustände, Pfade, Befunde |

### 19.2 Übernommene Informationen

Identität und Zweck · Nutzer und Arbeitsweise · Architektur und Verbindungsrichtung ·
Serverpfade und die vier Abweichungen von der Erwartung · Gedächtnisstruktur in drei
Ebenen · Anbieterzustände · ElevenLabs-Rangfolge und offene Leitung · Master und
gelernte Spezialisten · Hintergrundaufträge und der n8n-Befund · WhatsApp über den PC ·
Dashboard und Schnittstellen · Kopplung, Token und Autostart · zehn Entscheidungen
mit Begründung · zehn technische Erkenntnisse · neun offene Punkte.

### 19.3 Gefundene Duplikate

Zwischen den *tatsächlichen* Quellen zusammengeführt:

1. **Verbindungsrichtung zum PC** — in `STRUKTUR.md`, `readme.md` und
   `desktop_bridge.py` beschrieben → einmal, in Abschnitt 3.
2. **„Ein Gehirn statt zwei"** — in `readme.md` und `control_plane.py` →
   einmal, in Abschnitt 1.
3. **Grenze des Hauptgedächtnisses (30)** — als Konstante, im Modul-Docstring und
   in zwei Fehlermeldungen → einmal, in Abschnitt 6.
4. **„Geheimnisse bleiben auf dem Server"** — in `CLAUDE.md`, `.env.example` und
   `voice_service.py` → einmal, in Abschnitt 15.
5. **WhatsApp braucht keine Zugangsdaten** — in `.env.example` und im
   Integrationsadapter → einmal, in Abschnitt 12.

### 19.4 Gefundene Widersprüche

**KONFLIKT – MUSS GEPRÜFT WERDEN** *(beide Varianten erhalten)*

1. **n8n-Adresse**
   - Variante A: `N8N_BASE_URL=http://n8n:5678` *(Container nebenan, antwortet 401)*
   - Variante B: `https://reyesservice.app.n8n.cloud` *(als MCP-Server eingetragen, antwortet 200)*
   - **Bewertung:** B ist mit hoher Wahrscheinlichkeit richtig, da dort die
     Workflows liegen. A ist nicht erfunden — dort läuft tatsächlich ein n8n,
     nur ein anderes. Beide bleiben hier stehen, bis der Nutzer entscheidet.

2. **Pfad des Quelltexts auf dem Server**
   - Variante A: `/root/Mark-LIII` *(in früheren Anleitungen angenommen — existiert, ist aber nicht die laufende Installation)*
   - Variante B: `/root/jarvis` *(geprüft: dort liegen `.env`, Compose-Datei und Container)*
   - **Bewertung:** B ist die laufende Installation. A existiert daneben und
     könnte ein zweiter Klon sein — ungeklärt, ob er noch gebraucht wird.

3. **Anrede an den Nutzer**
   - Variante A: `mein Herr` *(Voreinstellung im Code)*
   - Variante B: Der Nutzer wurde in dieser Zusammenarbeit nie so angesprochen.
   - **Bewertung:** Unklar, ob die Voreinstellung je geändert wurde. Steht in der
     Datenbank des Servers, nicht im Quelltext.

### 19.5 Möglicherweise veraltete Informationen

| Angabe | Warum fraglich |
|---|---|
| Anbieterzustände (Abschnitt 7) | Momentaufnahme vom 18.09.2026, kann sich mit jedem Schlüssel ändern |
| „GitHub nicht verbunden" | Der Token war eingetragen, nur nicht geladen — nach dem Ausrollen vermutlich grün |
| „Lokaler Kalender offline" | Die Ursache ist behoben; gilt erst nach dem nächsten Bauen |
| `/root/Mark-LIII` als Serverpfad | überholt, siehe Konflikt 2 |
| „Master Agent: API key rejected" | Zustand vom 18.09.2026; inzwischen meldet Anthropic `healthy` |

### 19.6 Unklare Informationen

1. **Wo liegen die acht Quelldateien?** Vermutlich auf dem Windows-PC oder in
   einem anderen Projekt. Solange sie fehlen, sind die Abschnitte 5 und 11
   inhaltlich leer.
2. **Qdrant** — im Repo nicht vorhanden. Geplant, anderswo im Einsatz, oder eine
   Verwechslung?
3. **Brain / Graphify** — außer dem Anzeigenamen in `inventory.py` gibt es nichts.
4. **Welches Ollama-Modell** gewünscht ist, steht nirgends.
5. **`ELEVENLABS_VOICE_ID`** — welche Stimme gewählt wurde, ist nicht dokumentiert.
6. **Zweiter Klon unter `/root/Mark-LIII`** — wird er noch gebraucht?
7. **Das Desktop-Gedächtnis** (`memory/memory_manager.py`) und das Gedächtnis des
   Servers sind getrennt. Ob sie abgeglichen werden sollen, ist nicht entschieden.

---

*Ende. Sobald die acht Quelldateien vorliegen, gehören ihre dauerhaften Inhalte
in die Abschnitte 1–18; dieses Protokoll bleibt als Abschnitt 19 stehen.*
