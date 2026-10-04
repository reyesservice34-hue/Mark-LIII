# Loesungsdatenbank (Kapitel 29)

Wird bei jedem neuen Fehler zuerst durchsucht (Kap. 28.1 Schritt 3).
Format: JSONL, eine Zeile je Eintrag.

Geloester Fehler:
{"art":"geloest","symptom":"...","ursache":"...","loesung":"...","datum":"2026-10-01T12:00:00Z","komponenten":["..."],"pruefergebnis":"..."}

Gescheiterter Weg:
{"art":"gescheitert","nicht_wiederholen":true,"symptom":"...","versuch":"...","warum_gescheitert":"...","datum":"2026-10-01T12:00:00Z","komponenten":["..."]}