# Strukturbaum

Zwei Hälften, ein Gehirn: der **Server** (Command Center) denkt, entscheidet und
merkt sich alles. Der **PC** (Desktop-App) ist Hand und Auge — er führt aus, was
nur vor Ort geht. Beide reden nur über `/v1` — der PC über `/v1/desktop/*`, alles andere
(Skript, Handy, n8n) über `/v1/commands`.

```
Mark-LIII/
│
├── main.py, ui.py, start.py, START-JARVIS.bat   Desktop-App (PyQt6, Gemini Live)
├── desktop_agent.py                             Läuft auf dem PC, holt Aufträge vom Server
├── docker-compose.command-center.yml            Startet den Server
├── CLAUDE.md                                    Arbeitsregeln für dieses Repository
│
├── core/                     Gemeinsamer Kern von Desktop und Server
│   ├── prompt.txt              Persona + AUTONOMY-Regeln
│   ├── agency.py               Agentur-Rollen auf dem Desktop
│   ├── control_plane.py        Client für /v1/commands (Desktop → Server)
│   ├── desktop_runner.py       Führt Aufträge aus: kennt actions/ UND plugins/
│   ├── action_loader.py        Findet actions/ automatisch
│   ├── plugin_loader.py        Findet plugins/ automatisch
│   ├── llm_client.py, free_llm.py, stt.py, tts.py, speech_out.py, wake_word.py
│   ├── audio_devices.py, confirm.py, undo.py, installer.py, desktop_bridge.py
│
├── actions/                  21 Dateien, davon 17 mit TOOL-Dict (Rest: Hintergrunddienste)
│   ├── open_app.py             Programme öffnen
│   ├── computer_control.py     Maus, Tastatur, Fenster
│   ├── computer_settings.py    Lautstärke, Display, Energie
│   ├── browser_control.py      Browser steuern
│   ├── desktop.py, file_controller.py, file_processor.py
│   ├── screen_processor.py     Bildschirm lesen
│   ├── send_message.py, web_search.py, weather_report.py, flight_finder.py
│   ├── system_monitor.py, background_monitor.py, proactive.py, reminder.py
│   ├── code_helper.py, dev_agent.py, agency_agent.py
│   ├── game_updater.py, youtube_video.py
│
├── plugins/                  6 Plugins + Vorlage, je Datei ein PLUGIN-Dict
│   ├── whatsapp_voice.py       WhatsApp über WhatsApp Web (einmal per QR gekoppelt)
│   ├── email_box.py            Postfach am PC
│   ├── calendar.py, _calendar_core.py, _calendar_google.py
│   ├── home_assistant.py, printer_3d.py, quiz.py
│   └── _template.py            Vorlage für neue Plugins
│
├── memory/                   Desktop-Gedächtnis (memory_manager, config_manager)
├── dashboard/                Altes kleines Desktop-Dashboard (unberührt)
│
├── command_center/           ── DER SERVER ──
│   ├── install.sh              Installation auf dem Server (ein Befehl)
│   ├── Dockerfile              Multi-Stage: baut Frontend, packt Backend
│   ├── .env.example            Jede Einstellung, jedes Geheimnis — nur auf dem Server
│   ├── README.md               Betrieb, Kopplung, Reverse Proxy
│   ├── STRUKTUR.md             Diese Datei
│   │
│   ├── backend/              FastAPI + SQLite (WAL)
│   │   ├── app.py              Baut alles zusammen: Middleware, Module, SPA
│   │   ├── config.py           Nur aus Umgebungsvariablen, Secrets nie im Objekt
│   │   ├── db.py               Schema v2: 29 Tabellen (Chat, Agenten, Tasks,
│   │   │                       Approvals, Audit, Memory, Recordings, Prozeduren …)
│   │   ├── auth.py             scrypt, Session-Cookie (HttpOnly), Maschinen-Token
│   │   ├── deps.py             Principal, CSRF, Rollen
│   │   ├── events.py           Event-Bus → Server-Sent Events
│   │   ├── logbook.py          Ein Logbuch für alles
│   │   │
│   │   ├── ai/                 Anbieter austauschbar
│   │   │   ├── anthropic_provider.py   Claude
│   │   │   ├── openai_compat.py        OpenAI + alles Kompatible (auch lokal)
│   │   │   ├── gemini_provider.py      Gemini
│   │   │   └── base.py                 Gemeinsamer Vertrag
│   │   │
│   │   ├── orchestrator/       Der Master Agent
│   │   │   ├── runtime.py              Denkschleife, Delegation, Approval-Gate
│   │   │   ├── tool_registry.py        49 Werkzeuge, davon 33 ohne Zugangsdaten nutzbar
│   │   │   ├── agent_registry.py       Spezialisten + ihre Gesundheit
│   │   │   └── builtin_tools.py        Was der Server selbst kann
│   │   │
│   │   ├── services/           Die echte Arbeit
│   │   │   ├── desktop_bridge.py       Aufträge an den PC (Long-Poll, kein Port am PC)
│   │   │   ├── teaching.py             Aufnahme → Prozedur → Spezialist
│   │   │   ├── calendar_service.py     Google Calendar oder lokaler Speicher
│   │   │   ├── email_service.py        IMAP lesen, SMTP senden
│   │   │   ├── external.py             GitHub, Websuche
│   │   │   ├── voice_service.py        STT/TTS gegen offene Endpunkte
│   │   │   ├── approvals.py            Freigaben, die wirklich blockieren
│   │   │   ├── metrics.py              CPU, RAM, Platte, Docker, Dienste
│   │   │   ├── tasks.py, notifications.py, chat_store.py, files.py
│   │   │
│   │   ├── adapters/           integrations.py (Zustand: verbunden / nicht konfiguriert)
│   │   │                       workflows.py (n8n)
│   │   │
│   │   └── modules/            Je Ordner ein Bereich, meldet sich selbst an
│   │       ├── chat/  agents/  tasks/  automations/  workflows/
│   │       ├── desktop/        /v1/desktop/* — Warteschlange für den PC
│   │       ├── teach/          Aufnehmen und Lernen
│   │       ├── approvals/  notifications/  integrations/  server/
│   │       ├── files/  logs/  analytics/  settings/  health/  events/
│   │       ├── auth/           Anmeldung, Rollen
│   │       └── gateway/        /v1/commands — die Tür von außen (Skript, Handy, n8n)
│   │
│   └── frontend/             Vite + React + TypeScript
│       └── src/
│           ├── app/            Shell, Sidebar, Statusleiste, Befehlspalette, Sprache
│           ├── design/         tokens.css, base.css — fast schwarz, Graphit, Cyan
│           ├── lib/            API, Auth, Events, Store, Icons, Markdown
│           ├── components/     ui.tsx — ein Satz Bausteine für alle Seiten
│           └── modules/        Eine Seite je Bereich, spiegelt die Backend-Module
│               ├── home/  chat/  agents/  tasks/  automations/  workflows/
│               ├── desktop/  teach/  approvals/  notifications/
│               ├── integrations/  server/  files/  logs/  analytics/
│               └── settings/  login/
│
└── tests/                    Offline: kein Netz, keine Schlüssel, keine Konten
    ├── test_control_plane.py          Desktop-Client-Vertrag
    ├── test_command_center.py         Server end-to-end
    ├── test_command_center_tools.py   Kalender, Mail, GitHub, Suche, Sprache, Packaging
    └── test_command_center_teach.py   Fernsteuerung, Aufnahme, Lernen
```

## Wie die Teile zusammenspielen

```
  Browser ──HTTPS──▶ Reverse Proxy ──▶ Server :8080
                                        │
                                        ├── Master Agent (denkt, delegiert)
                                        ├── Werkzeuge (Kalender, Mail, GitHub, …)
                                        ├── Approval-Gate (blockiert wirklich)
                                        └── SQLite in /data (Docker-Volume)
                                              ▲
                                              │  Auftrag wartet in der Warteschlange
                                              │
  Dein PC ──ausgehend──▶ POST /v1/desktop/register   einmal beim Start
          ──ausgehend──▶ GET  /v1/desktop/poll       hängt bis zu 60 s offen
          ◀──Auftrag──   z. B. „öffne Photoshop", „schick WhatsApp an Meier"
          ──Ergebnis──▶ POST /v1/desktop/result
```

Wichtig daran: **der Server ruft den PC nie an.** Der PC fragt nach. Deshalb
braucht dein Anschluss zuhause keine offene Portfreigabe und keine feste IP.

## Wo was liegt, wenn es läuft

| Ort | Inhalt |
|---|---|
| Docker-Volume `jarvis-cc-data` → `/data` | Datenbank, Sitzungen, Gedächtnis, gelernte Agenten |
| `/data/workspace` | Dateien, die das Dashboard verwaltet — nur dieser Ordner, nichts darüber |
| `command_center/.env` | Alle Zugangsdaten. Nur auf dem Server, `chmod 600`, nie im Git |
| `command_center/backend/static/` | Das gebaute Frontend (erzeugt Vite beim Docker-Build) |

## Die drei Wege nach innen

1. **Browser** — `https://jarvis.deine-domain.de`, Anmeldung mit Benutzer und Passwort.
2. **Dein PC** — Maschinen-Token aus den Einstellungen, als `JARVIS_GATEWAY_TOKEN`
   auf dem PC gesetzt. Damit meldet er sich an, holt Aufträge und liefert Ergebnisse.
3. **`/v1/commands`** — die Tür für alles andere (Skript, Handy-Shortcut, n8n).
   Ein Token, dieselben Rollen, dieselbe Freigabepflicht.
