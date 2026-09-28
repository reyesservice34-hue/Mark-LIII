# Mia Dashboard — Aufbau, UI-Actions, Zustände

Ein zentraler Server (`main.py` + `dashboard/server.py`), mehrere Clients: Desktop (`/desktop`),
Handy/Browser (`/`), Login (`/login`). Alle sprechen mit **derselben** laufenden Instanz — im Dashboard
läuft kein eigener KI-Kern.

## Dateien

| Datei | Zweck |
|---|---|
| `server.py` | FastAPI-Routen, WebSocket, QR/Geräte-Kopplung, AES, Upload, **UI-Actions**, strukturierte Logs |
| `static/mia.css` | Gemeinsames Glas-Designsystem (Tokens, Panels, Buttons, Chat, Banner) |
| `static/mia-desktop.css` | Layout des Desktop-Control-Centers |
| `static/mia-core.js` | `MiaState` (Zustandsmodell) + `MiaCore` (Canvas-Gehirn) |
| `static/mia-ui.js` | Router (Hash + History), UI-Action-Empfänger, Awareness-Report, sichtbare Fehler |
| `static/mia-desktop.js` | Desktop-Views, Datenlisten, Datei-Vorschau, Palette, Einstellungen |
| `static/shared.js` | Gemeinsame Logik Desktop + Handy: Auth, AES, WebSocket, Chat, Voice, Upload, Kalender/Standort |
| `static/app.html`, `desktop.html`, `login.html` | Seiten |

## Design-Regel

**Orange gehört ausschließlich dem Gehirn** (`mia-core.js`, `C`-Palette). Alles andere ist transluzentes
Weiß/Silber/Grau auf Schwarz; Grün/Rot nur für echte Erfolgs-/Fehlerzustände. Neue Farben nur als Token in
`mia.css` anlegen. Kontrolle: `full.py`-Orange-Audit (siehe Tests) prüft alle berechneten Farben außerhalb der Canvas.

## Zustandsmodell

`MiaState` löst benannte Signale nach Priorität zu genau einem Zustand auf:
`warning > speaking > processing_file > thinking > listening > syncing > device_connected > pairing > standby`.

| Zustand | Echtes Signal |
|---|---|
| `listening` | Mikrofon-Sitzung (`/ws/phone-audio`) offen, Pegel = echter Mikrofon-RMS |
| `thinking` | Nutzereingabe gesendet/gesprochen; Tool-Start (`activity`) außer Datei-Tools; endet mit erster Antwort |
| `speaking` | Audio-Chunks (`audio`) / Antworttext (`log_delta`/`log`); Pulsstärke = echter Ausgabepegel |
| `processing_file` | Upload läuft, `file_received`, Tool `file_processor`/`file_controller` |
| `syncing` | Kalender-/Companion-Abruf |
| `device_connected` | WebSocket gerade verbunden (2,6 s) |
| `pairing` | Login-Seite |
| `warning` | Verbindung getrennt, eingehender Anruf, Fahrzeit „spät“, Upload-Fehler |

Der Zustand ändert **nie die Farbe** des Gehirns, nur Intensität, Fluss, Partikel, Ringe, Glow.
`prefers-reduced-motion` (oder Einstellungen → „Reduziert“) schaltet auf Standbilder um.

## UI-Actions (Mia → Dashboard)

Kein Freitext, kein Code, kein Shell: strikte Whitelist auf Server **und** Client.

```json
{"type":"ui_action","id":"a1b2c3d4e5f6","action":"open_view","target":"calendar"}
```

| Aktion | Ziel | Wirkung |
|---|---|---|
| `open_view` | `dashboard, chat, tasks, calendar, files, notes, location, devices, status, settings` | Ansicht wechseln (History-Eintrag) |
| `open_file` | Dateiname aus dem Upload-Ordner | Datei-Ansicht, Vorschau (Bild/Text) oder Metadaten + Download |
| `open_task` | Titel oder ID | Aufgabenansicht, Eintrag markiert |
| `open_calendar_event` | Titel oder ID | Kalenderansicht, Eintrag markiert (Google-Kalender oder eigene Termine) |
| `show_notification` | Text (≤ 240 Zeichen), Level | Sichtbare Meldung, nur `textContent` |
| `focus_chat` | – | Chat öffnen, Eingabe fokussieren |

`open_project` / `open_device` werden ausdrücklich als „nicht unterstützt“ abgelehnt (kein Projekte-Feature,
keine öffnbaren Einzelgeräte).

**Ablauf:** Tool `dashboard_open` (main.py) → `DashboardServer.send_ui_action` validiert → `broadcast_event`
(nicht im Verlauf gespeichert, wird nie später erneut abgespielt) → Client `MiaUI.handleAction` → Ergebnis als
`ui_ack` zurück → das Tool meldet Mia **Erfolg oder Grund des Scheiterns**. Ohne Bestätigung nach 4 s:
`no_confirmation`. Fehler sind immer sichtbar (roter Hinweis im Dashboard), im Client-Log (Systemstatus) und im
Server-Log (`ui_action_failed`).

`open_file` löst Namen **ausschließlich** im Upload-Ordner auf (`_resolve_upload`: keine Trenner, keine
Symlinks, `resolve()`-Prüfung). Der Client bekommt nur Name/Größe/Zeit, nie einen Serverpfad.

## Dashboard-Awareness (Dashboard → Mia)

Der Client meldet über den WebSocket `ui_state` (Ansicht, Auswahl, Zählwerte, Tab sichtbar?). Mia kann das
über das Tool `dashboard_context` abfragen. Das ist der **eigene Zustand** der Oberfläche — kein
Bildschirmfoto, kein Desktop-Zugriff. Bildschirm-Awareness wäre eine getrennte, ausdrücklich aktivierbare,
sichtbare und abschaltbare Funktion und ist **nicht** vorhanden.

## Logging

`_log_event(level, event, **fields)` schreibt eine JSON-Zeile mit Präfix `[Dashboard]`:
`ui_action_ok`, `ui_action_failed`, `ui_client_report`, `auth_failed`, `ws_send_failed`, `ws_bad_message`,
`file_open_failed`. Nie geloggt: Tokens, Sitzungsschlüssel, API-Schlüssel, Nachrichtentexte, Dateiinhalte.

## Namensgebung

Der angezeigte Name kommt aus `config/api_keys.json` → `assistant_name` (derselbe Schlüssel, den `main.py` für
die Stimme/Persona nutzt), Standard „Mia“. Technische Bezeichner bleiben bewusst unverändert, damit bestehende
Kopplungen und Daten weiterleben: `jarvis_token`, `jarvis_key`, `jarvis_device_token` (Browser-Speicher),
AES-Salt `JARVIS-DASHBOARD-v1`, Ordner `JARVIS Uploads`, Service `mark-liii`.

## Bekannte Grenzen

- Geräte-Kopplungen werden in `config/device_sessions.json` (Modus 0600) gespeichert. Aktive Sitzungs-Tokens
  (`_tokens`) leben im Arbeitsspeicher; „Widerrufen“ verhindert das automatische Wiederanmelden, beendet
  laufende Sitzungen aber erst beim Neustart.
- Eine Aufgaben-/Projekte-/Erinnerungen-Ansicht gibt es nur, wo die Funktion real existiert (Aufgaben, Notizen,
  eigene Termine, Google-Kalender). Projekte, Erinnerungen und KI-Tools fehlen bewusst.
- Der Google-Kalender braucht `config/companion-calendar.json`; ohne sie zeigt die Oberfläche „nicht eingerichtet“.
