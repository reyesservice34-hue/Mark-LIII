# Hybrid-Betrieb: Dienste auf dem Server, Modelle auf dem eigenen Rechner

Ziel: keine laufenden Modellkosten. Der günstige Server (z. B. Hetzner CX43/CAX31, ca. 16-21 €/Monat)
hält Gedächtnis und Dienste, der eigene Rechner liefert die Rechenleistung für Qwen3.

| Läuft auf dem Server (immer an) | Läuft auf dem Rechner (GPU/CPU) |
|---|---|
| Command Center, Mias Logik | Ollama mit `qwen3:14b` (oder `qwen3:8b`) |
| Qdrant, SQLite (Gedächtnis) | Embedding-Modell |
| n8n, WhatsApp/Twilio | Spracherkennung/-ausgabe (optional) |
| OmniRoute (Verteiler) | |

## 1. Tailscale (Verbindung ohne Portfreigabe)
1. Tailscale auf dem Rechner und dem Server installieren, mit demselben Konto anmelden.
2. IP des Rechners notieren (`tailscale ip -4`), z. B. `100.x.y.z`.

## 2. Ollama auf dem Rechner
1. Ollama installieren, `OLLAMA_HOST=0.0.0.0` setzen (nur im Tailscale-Netz erreichbar lassen, keine Router-Freigabe).
2. `ollama pull qwen3:14b` (bei wenig VRAM `qwen3:8b`).
3. Vom Server prüfen: `curl http://100.x.y.z:11434/api/tags`.

## 3. Server umstellen
1. In OmniRoute den Ollama-Anbieter auf `http://100.x.y.z:11434` setzen.
2. `command_center/free-mode.env.template` nach `command_center/.env` kopieren, `OMNIROUTE_API_KEY` eintragen.
3. `docker compose -f docker-compose.command-center.yml up -d`

## Hinweis
Ist der Rechner aus, fällt Qwen aus. `MIA_CHAT_LOCAL_FALLBACK=1` greift nur, wenn auf dem Server
ein kleines lokales Modell läuft (Profil `ollama` in `docker-compose.command-center.yml`).
Nicht getestet; Hardware und Modellgröße hängen vom Rechner ab.
