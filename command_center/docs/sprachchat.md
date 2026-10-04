# Sprachchat – Aufbau und Einstellungen

Stand: 4. Oktober 2026

## Ein Gehirn

Gesprochenes geht durch denselben Eingang wie getippter Text (`send_message`). Damit gelten für Sprache und Text
dasselbe Modell, dasselbe Gedächtnis und Verhaltensgedächtnis, dieselbe Verständnisschicht
(`services/communication.py`), dieselben Werkzeuge, Erinnerungen (`task.remind`) und derselbe Gesprächsverlauf.

## Ablauf eines gesprochenen Satzes

1. Der Browser schickt das Mikrofon an `/api/voice/live` (`modules/live/__init__.py`).
2. Der Server erkennt das Satzende nach 1 s Stille (`MIA_VOICE_END_SILENCE_MS`).
3. **Spracherkennung**: OpenAI `gpt-4o-mini-transcribe` (~1 s), mit Fachwortschatz als Hinweis.
   Fällt OpenAI aus, übernimmt das lokale Whisper (Speaches).
4. **Denken**: MIA wie im Text-Chat (Haiku-Kette).
5. **Stimme**: OpenAI `gpt-4o-mini-tts`, Stimme `nova`. Ersatz: Gemini (Aoede), dann Piper.
   Die Antwort wird satzweise vertont, der erste Satz klingt, während die nächsten entstehen.

Erwartete Zeit bis zum ersten Ton: etwa 3–5 s.

## Standby und „Hey Mia“

- Nach dem Öffnen und nach 45 s ohne Gespräch ist MIA im Standby und reagiert nur auf „Hey Mia“
  (auch „Okay Mia“, „Hallo Mia“). Der Satz danach wird gleich beantwortet: „Hey Mia, was steht heute an?“
- „Tschüss“ oder „das war's“ schickt sie nach ihrer Antwort in den Standby.
- Getippte Nachrichten und wichtige Meldungen wecken sie ebenfalls.
- Sprechen, während MIA arbeitet, bricht ihre Arbeit **nicht** ab; es wird danach beantwortet.
  Abgebrochen wird nur mit „Stopp“, „Halt“ oder „Abbrechen“ am Satzanfang.

## Schalter in `command_center/.env`

| Variable | Wert | Wirkung |
|---|---|---|
| `MIA_VOICE_LIVE` | `local` | Sprachchat über MIAs Gehirn (empfohlen). `realtime` = OpenAI Realtime, eigenes Modell, schneller, aber nicht MIA. |
| `JARVIS_CC_STT_PROVIDER` | `openai` | Spracherkennung über OpenAI; `local` = nur lokales Whisper (langsamer, bleibt auf dem Server). |
| `JARVIS_CC_TTS_PROVIDER` | `openai` | Stimme über OpenAI; `gemini` = Aoede (gratis nur 10 Anfragen/Tag). |
| `JARVIS_CC_OPENAI_TTS_VOICE` | `nova` | Stimme, z. B. `coral`, `shimmer`, `sage`. |
| `JARVIS_CC_OPENAI_TTS_INSTRUCTIONS` | (Standard) | Tonfall der Stimme. |
| `MIA_VOICE_WAKEWORD` | `1` | `0` = kein Standby, immer wach. |
| `MIA_VOICE_IDLE_S` | `45` | Sekunden ohne Gespräch bis zum Standby. |
| `MIA_VOICE_END_SILENCE_MS` | `1000` | Stille, nach der ein Satz als beendet gilt. |
| `MIA_VOICE_PROVIDER` | (aus) | Gesetzt bekäme Sprache ein eigenes Modell – nicht setzen, sonst zwei Gehirne. |

Änderungen an der `.env` wirken erst nach:

```bash
cd /root/Mark-LIII && docker compose -f docker-compose.command-center.yml up -d jarvis-command-center
```

## Fehlersuche

Jeder Schritt steht im Protokoll des Containers:

```bash
docker logs --since 30m jarvis-command-center 2>&1 | grep MIA_VOICE_LATENCY
```

| `stage` | Bedeutung |
|---|---|
| `stt` | Erkennung fertig (`stt_ms`, `awake`) |
| `stt_failed` | Erkennung ohne Text oder Fehler |
| `wake_check` | Weckwort geprüft (`matched`) |
| `wake` / `standby` | MIA wach / im Standby |
| `stop_cancel` | Lauf per „Stopp“ abgebrochen |
| `reply` | Antwort fertig (`chat_ms`, `tts_ms`) |
| `response_end` | Runde beendet (`completed`) |
| `disconnect_during_turn` | Verbindung brach während einer Antwort ab |

## Kosten

OpenAI-Erkennung ca. 0,3 Cent und Stimme ca. 1–2 Cent pro Minute Sprache, dazu das Chat-Modell wie beim Tippen.
