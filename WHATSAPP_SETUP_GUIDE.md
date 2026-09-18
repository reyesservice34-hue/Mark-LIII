# 📱 WhatsApp + JARVIS Setup Guide
**Dein lokaler KI-Assistent über WhatsApp**

---

## 🚀 Quick Start (Lokal testen - keine Twilio nötig)

### 1. Starte die Services
```bash
cd ~/Mark-LIII
./jarvis_whatsapp_start.sh
```

**Expected Output:**
```
✨ JARVIS WHATSAPP INTEGRATION ONLINE

📊 Services Running:
   🤖 JARVIS Coordinator API: http://localhost:8000
   📱 WhatsApp Gateway: http://localhost:5000
```

### 2. Test via HTTP (lokal)
```bash
# In neuem Terminal:
./test_whatsapp_integration.sh
```

### 3. Sende eine Anweisung
```bash
curl -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "du@example.com",
    "instruction": "Erstelle einen Preisplan",
    "channel": "test"
  }'
```

**JARVIS antwortet:**
```
🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, angebot, reviewer, prompt_architect
✅ executor: Befehl ausgeführt: Erstelle einen Preisplan
✅ angebot: Preisangebot erstellt
✅ prompt_architect: Prompt erstellt und optimiert
✅ reviewer: Qualitätsprüfung bestanden
✨ Alle Schritte abgeschlossen
```

---

## 📱 Production Setup (Mit Twilio für echter WhatsApp)

### 1. Twilio Account erstellen
- Geh zu: https://www.twilio.com
- Kostenlos anmelden
- Navigiere zu: **Messaging → WhatsApp → Try it out**

### 2. WhatsApp Sandbox aktivieren
- Akzeptiere die Sandbox-Einladung von Twilio
- Du erhältst eine Twilio WhatsApp Nummer

### 3. Credentials besorgen
```bash
# Twilio Console:
# 1. Gehe zu Account → Settings
# 2. Kopiere Account SID
# 3. Kopiere Auth Token
# 4. Notiere deine WhatsApp Nummer
```

### 4. Update .env Datei
```bash
cat >> .env << 'EOF'
TWILIO_ACCOUNT_SID=ACxxx...
TWILIO_AUTH_TOKEN=xxx...
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155552671
JARVIS_COORDINATOR_URL=http://localhost:8000
EOF
```

### 5. Webhook in Twilio konfigurieren
```
Twilio Console → Phone Numbers → Select your WhatsApp number
↓
Scroll to: "When a message comes in"
↓
Set URL to: https://your-domain.com/whatsapp/webhook
↓
Save
```

### 6. Starte Services
```bash
./jarvis_whatsapp_start.sh
```

---

## 🏗️ Architektur

```
Du (WhatsApp)
    ↓
[Twilio API]
    ↓
WhatsApp Gateway (:5000)
    ├→ Empfängt Nachrichten
    ├→ Speichert History
    └→ Delegiert an JARVIS
    
JARVIS Coordinator API (:8000)
    ├→ Empfängt Anweisungen
    ├→ Analysiert mit prompt_architect
    ├→ Delegiert an passende Agenten
    └→ Formatiert Antwort
    
10-Agent Agency System
    ├→ prompt_architect (Prompt Engineering)
    ├→ executor (Befehle ausführen)
    ├→ reviewer (Qualität prüfen)
    ├→ angebot (Preise & Angebote)
    ├→ dispo (Planung & Zeitplan)
    ├→ kunde (Kundenkomm.)
    ├→ recherche (Research)
    ├→ technik (Code & Automation)
    ├→ berater (Beratung)
    └→ coordinator (Orchestrierung)
    
Response zurück zu Dir
```

---

## 💬 Beispiel Konversationen

### Beispiel 1: Preisplanung
**Du:** "Erstelle einen Preisplan für ein neues Projekt"

**JARVIS:**
```
🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, angebot, prompt_architect, reviewer
✅ executor: Befehl ausgeführt: Erstelle einen Preisplan für ein neues Projekt
✅ angebot: Preisangebot erstellt
✅ prompt_architect: Prompt erstellt und optimiert
✅ reviewer: Qualitätsprüfung bestanden
✨ Alle Schritte abgeschlossen
📝 Weitere Anweisungen über WhatsApp jederzeit möglich
```

### Beispiel 2: Projekt Planung
**Du:** "Plane den nächsten 2-Wochen Sprint"

**JARVIS:**
```
🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, dispo, kunde, prompt_architect
✅ executor: Befehl ausgeführt: Plane den nächsten 2-Wochen Sprint
✅ dispo: Zeitplan aktualisiert
✅ kunde: Kundenbenachrichtigung gesendet
✅ prompt_architect: Prompt erstellt und optimiert
✨ Alle Schritte abgeschlossen
```

### Beispiel 3: Technische Aufgabe
**Du:** "Schreibe ein Python-Skript für die Datenverarbeitung"

**JARVIS:**
```
🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, technik, prompt_architect, reviewer
✅ executor: Befehl ausgeführt: Schreibe ein Python-Skript für die Datenverarbeitung
✅ technik: Technische Umsetzung gestartet
✅ prompt_architect: Prompt erstellt und optimiert
✅ reviewer: Qualitätsprüfung bestanden
✨ Alle Schritte abgeschlossen
```

---

## 🔍 Troubleshooting

### Services starten nicht
```bash
# Logs prüfen
tail -f /tmp/jarvis_coordinator.log
tail -f /tmp/whatsapp_gateway.log

# Health check
curl http://localhost:8000/health
curl http://localhost:5000/health
```

### Twilio Nachrichten kommen nicht an
```bash
# 1. Credentials prüfen
grep TWILIO .env

# 2. Webhook URL accessible?
curl https://your-domain.com/whatsapp/webhook

# 3. Twilio Console überprüfen
# → Phone Numbers → Select number
# → Check webhook configuration
# → Check message logs
```

### Python Dependencies fehlen
```bash
pip install --upgrade flask requests python-dotenv
```

### Port bereits belegt
```bash
# Finde Prozess auf Port 8000
lsof -i :8000

# Oder beende alte Prozesse
pkill -f jarvis_coordinator_api.py
pkill -f whatsapp_gateway.py
```

---

## 📊 Monitoring

### Services überwachen
```bash
# Terminal 1: Coordinator logs
tail -f /tmp/jarvis_coordinator.log

# Terminal 2: Gateway logs
tail -f /tmp/whatsapp_gateway.log

# Terminal 3: Check agent status
watch -n 5 'curl -s http://localhost:8000/agent_status | python3 -m json.tool | head -20'
```

### Nachrichtenhistory abrufen
```bash
# Alle Anweisungen
curl http://localhost:8000/instruction_history | python3 -m json.tool

# Nur von dir
curl "http://localhost:8000/instruction_history?user_id=du@example.com" | python3 -m json.tool
```

---

## 🎯 Nächste Schritte

1. **Starte lokal:** `./jarvis_whatsapp_start.sh`
2. **Teste:** `./test_whatsapp_integration.sh`
3. **Twilio Setup:** Folge Production Setup Anleitung oben
4. **Voice Output:** Siehe `JARVIS_VOICE_OUTPUT.md` (Sprachausgabe via TTS)
5. **Desktop Integration:** Siehe `JARVIS_DESKTOP_AUTOMATION.md` (Automatisierung)

---

## 🔗 API Reference

### JARVIS Coordinator

**POST /process_instruction**
```bash
curl -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user@example.com",
    "instruction": "Deine Anweisung hier",
    "channel": "whatsapp"
  }'
```

**Response:**
```json
{
  "status": "ok",
  "user_id": "user@example.com",
  "response": "JARVIS Antwort...",
  "timestamp": "2026-09-17T13:30:00"
}
```

**GET /agent_status**
```bash
curl http://localhost:8000/agent_status
```

**GET /instruction_history**
```bash
curl "http://localhost:8000/instruction_history?user_id=user@example.com"
```

### WhatsApp Gateway

**POST /whatsapp/test** (Lokal testen)
```bash
curl -X POST http://localhost:5000/whatsapp/test \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "1234567890",
    "message": "Nachricht hier"
  }'
```

**GET /whatsapp/history/<number>**
```bash
curl http://localhost:5000/whatsapp/history/1234567890
```

**POST /whatsapp/webhook** (Twilio)
```
Twilio ruft das automatisch auf wenn Nachrichten kommen
```

---

## ❓ FAQ

**F: Funktioniert das ohne Twilio?**
A: Ja! Nutze den Test-Endpoint für lokale Tests. Twilio ist nur für echter WhatsApp nötig.

**F: Wo werden meine Nachrichten gespeichert?**
A: In `whatsapp_message_history.json` - lokal auf deinem Computer.

**F: Welche Agenten sind am aktivsten?**
A: Typischerweise `executor` und `prompt_architect` sind bei allen Aufgaben beteiligt.

**F: Kann ich neue Agenten hinzufügen?**
A: Ja! Bearbeite `scripts/jarvis_coordinator_api.py` und füge neue Agenten zu `AGENTS` hinzu.

**F: Funktioniert das ohne Internet?**
A: Ja für lokale Tests. Mit Twilio brauchst du Internet für WhatsApp.

---

**Status:** ✨ Bereit zum Starten  
**Letzte Aktualisierung:** 2026-09-17  
**Nächster Schritt:** `./jarvis_whatsapp_start.sh`
