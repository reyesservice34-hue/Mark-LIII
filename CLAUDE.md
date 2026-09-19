# Arbeitsweise in diesem Repository

## Wie gearbeitet wird (vom Nutzer ausdrücklich gewünscht, gilt immer)

**Eigenständig mitdenken, jedes Mal, ohne Aufforderung.** Nicht nur ausführen,
was dasteht, sondern sehen, was daraus folgt. Das heißt konkret:

- **Kleine Entscheidungen selbst treffen.** Welche Bibliothek, welche Reihenfolge,
  welcher Dateiname. Eine Rückfrage zu etwas, das dem Nutzer egal ist, kostet mehr
  Zeit als eine Entscheidung, die sich später korrigieren lässt.
- **Umwege und doppelte Wege vermeiden.** Bevor etwas neu gebaut wird: prüfen, ob es
  das schon gibt. Dieses Repository hat gewachsene Teile (`actions/`, `plugins/`,
  `core/`), die wiederverwendet gehören statt nachgebaut zu werden.
- **Lücken benennen und schließen, nicht dekorieren.** Kein Platzhalter, der so
  aussieht, als könne er etwas. Wenn etwas nicht geht, sagt es das und nennt den
  Grund. Was mit vertretbarem Aufwand echt gemacht werden kann, wird echt gemacht.
- **Weiterdenken als Teil der Aufgabe.** Wer eine Funktion baut, prüft, was sie im
  Zusammenspiel unbrauchbar macht. Beispiel: Die Fernsteuerung des PCs war nutzlos
  für WhatsApp, weil sie nur `actions/` kannte und WhatsApp ein Plugin ist. So etwas
  fällt auf, bevor der Nutzer es merkt.
- **Nur fragen, wenn ein Fehler teuer oder unumkehrbar ist.** Geld, Nachrichten an
  Kunden, gelöschte Daten, Produktionssysteme. Sonst: machen und danach berichten.
- **Nie einen Plan ankündigen statt zu arbeiten.** Erst tun, dann sagen, was passiert
  ist.

Antwortsprache: Deutsch, solange der Nutzer deutsch schreibt.

## Was hier liegt

| Pfad | Was es ist |
|---|---|
| `main.py`, `ui.py` | Desktop-App (PyQt6, Gemini Live), gewachsen, läuft |
| `actions/` | Desktop-Werkzeuge, je Datei ein `TOOL`-Dict, automatisch entdeckt |
| `plugins/` | Desktop-Plugins, je Datei ein `PLUGIN`-Dict, automatisch entdeckt |
| `core/` | Gemeinsamer Kern: Persona, Agentur, Control-Plane-Client, Desktop-Runner |
| `command_center/backend` | Server (FastAPI): Master Agent, Registries, Module, `/v1`-Gateway |
| `command_center/frontend` | Dashboard (Vite + React + TypeScript) |
| `tests/` | Offline-Harnesse ohne Netz, ohne Schlüssel, ohne Konten |

## Regeln, die nicht verhandelbar sind

- **Nichts vortäuschen.** Keine erfundenen Messwerte, keine Werkzeuge ohne
  Implementierung, kein „erledigt", wenn nichts lief. Ein Werkzeug ohne Zugangsdaten
  meldet, welche Variable fehlt.
- **Geheimnisse bleiben auf dem Server.** Nie in den Browser, nie ins Bundle, nie ins
  Git. `config/api_keys.json` und `command_center/.env` sind gitignored.
- **Bestehendes nicht kaputtmachen.** Die Desktop-App muss ohne Server weiterlaufen.
  Änderungen an `main.py` bleiben klein und optional zuschaltbar.
- **Vor dem Commit:** `python tests/test_*.py` (alle), im Frontend
  `npx tsc --noEmit`, `npx eslint src --ext .ts,.tsx`, `npx vite build`.

## Tests laufen lassen

```bash
python tests/test_control_plane.py          # Desktop-Client-Vertrag
python tests/test_command_center.py         # Server end-to-end
python tests/test_command_center_tools.py   # Kalender, Mail, GitHub, Suche, Sprache
python tests/test_command_center_teach.py   # Desktop-Fernsteuerung, Lernen
python tests/test_extensions.py             # MCP-Server, Fähigkeiten, Selbstauskunft
python tests/test_live_line_ui.py           # offene Leitung im Browser (braucht node + Playwright)
cd command_center/frontend && npx tsc --noEmit && npx eslint src --ext .ts,.tsx && npx vite build
```
