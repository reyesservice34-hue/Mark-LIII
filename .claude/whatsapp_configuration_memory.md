# 📱 WhatsApp Configuration Memory
**Persistent Configuration & Setup Details**

---

## Current Setup Status

### Environment Variables
```
TWILIO_ACCOUNT_SID=        [Configured in .env]
TWILIO_AUTH_TOKEN=         [Configured in .env]
TWILIO_WHATSAPP_NUMBER=    whatsapp:+14155552671 [Twilio Sandbox]
JARVIS_COORDINATOR_URL=    http://localhost:8000
```

### Service Endpoints
```
WhatsApp Gateway:       http://localhost:5000
JARVIS Coordinator:     http://localhost:8000
Health Check:           GET http://localhost:8000/health
Agent Status:           GET http://localhost:8000/agent_status
```

---

## How WhatsApp Integration Works

### Architecture Flow

```
1. USER SENDS MESSAGE
   └─ "Erstelle einen Preisplan"
      ↓
2. MESSAGE ARRIVES AT TWILIO
   └─ Via WhatsApp app on your phone
      ↓
3. TWILIO WEBHOOK CALLS OUR GATEWAY
   └─ POST http://your-domain/whatsapp/webhook
      └─ Twilio sends: {From: "+1234567890", Body: "Erstelle..."}
      ↓
4. WHATSAPP GATEWAY PROCESSES
   ├─ Receives message
   ├─ Extracts user number & text
   ├─ Stores in message history
   └─ Sends to JARVIS Coordinator
      ↓
5. JARVIS COORDINATOR PROCESSES
   ├─ Analyzes instruction with prompt_architect
   ├─ Creates delegation plan
   ├─ Activates relevant agents:
   │  ├─ prompt_architect (analysis)
   │  ├─ executor (execution)
   │  ├─ reviewer (verification)
   │  ├─ angebot (if pricing related)
   │  └─ others (based on task type)
   └─ Formats response
      ↓
6. RESPONSE SENT BACK
   ├─ Via WhatsApp Gateway
   ├─ Via Twilio API
   └─ You receive on WhatsApp app
      ↓
7. CONVERSATION CONTINUES
   └─ Full history stored in whatsapp_message_history.json
```

---

## Step-by-Step: How Your Message Gets Processed

### Step 1: Reception
**Your Message:** "Erstelle einen Preisplan für mein neues Projekt"

**What Happens:**
- WhatsApp app on your phone sends to WhatsApp cloud
- Twilio receives it
- Twilio webhook calls our gateway

### Step 2: Gateway Processing
**WhatsApp Gateway (port 5000):**
```python
# Receives from Twilio:
{
  "From": "whatsapp:+491234567890",  # Your number
  "Body": "Erstelle einen Preisplan für mein neues Projekt"
}

# Stores in history:
{
  "timestamp": "2026-09-17T14:30:00Z",
  "from": "+491234567890",
  "message": "Erstelle einen Preisplan...",
  "type": "incoming"
}

# Sends to Coordinator:
{
  "user_id": "+491234567890",
  "instruction": "Erstelle einen Preisplan für mein neues Projekt",
  "channel": "whatsapp",
  "timestamp": "2026-09-17T14:30:00Z"
}
```

### Step 3: JARVIS Coordinator Analysis
**Coordinator (port 8000):**
```
1. ANALYSIS (prompt_architect)
   └─ Detects keywords: "Preisplan", "Projekt"
   └─ Category: "pricing"
   └─ Complexity: "high"
   └─ Requires OpenAI: true

2. DELEGATION PLAN
   └─ Agents needed:
      ├─ executor (primary)
      ├─ angebot (pricing specialist)
      ├─ prompt_architect (analysis)
      └─ reviewer (quality check)

3. EXECUTION
   └─ Each agent processes task
   └─ Results: pricing plan created, verified

4. RESPONSE FORMATTING
   └─ Collects all results
   └─ Creates human-readable response
   └─ Sends back to gateway
```

### Step 4: Response Delivery
**Gateway sends back via Twilio:**
```
Message to you:
"🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, angebot, prompt_architect, reviewer

✅ executor: Befehl ausgeführt: Erstelle einen Preisplan für mein neues Projekt
✅ angebot: Preisangebot erstellt
✅ prompt_architect: Prompt erstellt und optimiert
✅ reviewer: Qualitätsprüfung bestanden

✨ Alle Schritte abgeschlossen
📝 Weitere Anweisungen über WhatsApp jederzeit möglich"
```

**You receive:** Response on WhatsApp within seconds

---

## Testing Without Twilio (Local)

### No Credentials Needed
```bash
./test_whatsapp_integration.sh
```

### Manual Test
```bash
# Send instruction directly to Coordinator
curl -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test@example.com",
    "instruction": "Erstelle einen Preisplan",
    "channel": "test"
  }'

# JARVIS processes and responds immediately
```

### WhatsApp Test (without Twilio API)
```bash
# Send to WhatsApp gateway test endpoint
curl -X POST http://localhost:5000/whatsapp/test \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "+491234567890",
    "message": "Hallo JARVIS"
  }'
```

---

## Production Setup (With Real WhatsApp)

### 1. Get Twilio Account
- Sign up: https://www.twilio.com
- Free trial includes WhatsApp sandbox
- Get credentials (SID, Auth Token)

### 2. Configure .env
```bash
export TWILIO_ACCOUNT_SID="ACxxx..."
export TWILIO_AUTH_TOKEN="xxx..."
export TWILIO_WHATSAPP_NUMBER="whatsapp:+14155552671"
export JARVIS_COORDINATOR_URL="http://localhost:8000"
```

### 3. Setup Webhook in Twilio
```
Dashboard → Phone Numbers → Select WhatsApp number
↓
Messaging: When a message comes in
↓
URL: https://your-domain.com/whatsapp/webhook
↓
Method: POST
↓
Save
```

### 4. Start Services
```bash
cd ~/Mark-LIII
./jarvis_whatsapp_start.sh
```

**Result:** JARVIS responds to WhatsApp messages in real-time

---

## Message Types & Agent Delegation

### How Agents Are Chosen

Based on keywords in your message:

| Keywords | Agents Triggered | Example |
|----------|-----------------|---------|
| Preis, Kosten, Budget | `angebot`, `reviewer` | "Wie viel kostet das?" |
| Plan, Termin, Zeitplan | `dispo`, `kunde` | "Plane nächste Woche" |
| Recherche, Suche, Infos | `recherche`, `prompt_architect` | "Finde Informationen zu..." |
| Code, Script, Automatisierung | `technik`, `executor` | "Schreibe ein Python Skript" |
| Rat, Vorschlag, Strategie | `berater`, `prompt_architect` | "Gib mir einen Rat" |
| Kunde, Mitteilen, Kontakt | `kunde`, `prompt_architect` | "Benachrichtige den Kunden" |
| (Allgemein) | `executor`, `prompt_architect` | "Mach was" |

---

## Conversation History

### Where Messages Are Stored
```
whatsapp_message_history.json
├─ All messages (incoming + responses)
├─ By user number
├─ With timestamps
└─ Queryable by user_id
```

### Retrieve Your History
```bash
# All your messages
curl "http://localhost:8000/instruction_history?user_id=+491234567890" | python3 -m json.tool

# WhatsApp gateway history
curl "http://localhost:5000/whatsapp/history/491234567890"
```

---

## Troubleshooting WhatsApp Integration

### Issue: Messages not arriving
**Solution:**
```bash
# 1. Check Twilio webhook
curl https://your-domain.com/whatsapp/webhook -X POST

# 2. Check gateway logs
tail -f /tmp/whatsapp_gateway.log

# 3. Verify Twilio config
# Dashboard → Phone Numbers → Select number → Check URL
```

### Issue: JARVIS not responding
**Solution:**
```bash
# 1. Check coordinator logs
tail -f /tmp/jarvis_coordinator.log

# 2. Test coordinator health
curl http://localhost:8000/health

# 3. Check agent status
curl http://localhost:8000/agent_status | python3 -m json.tool
```

### Issue: Can't start services
**Solution:**
```bash
# Install dependencies
pip install flask requests python-dotenv

# Start services
./jarvis_whatsapp_start.sh

# If port already used
lsof -i :8000
lsof -i :5000
# Kill old processes
pkill -f jarvis_coordinator_api.py
```

---

## Next Steps

### Immediate (Today)
1. ✅ Start local services: `./jarvis_whatsapp_start.sh`
2. ✅ Test without Twilio: `./test_whatsapp_integration.sh`
3. ⏳ Setup Twilio (if you want real WhatsApp)

### Soon (This Week)
1. Deploy to server: `bash JARVIS_DEPLOYMENT_PACKAGE.sh`
2. Setup Ollama (local free LLM)
3. Add voice output (TTS)

### Later (This Month)
1. Desktop automation (proactive tasks)
2. Multi-user support
3. Advanced agent customization

---

**Status:** ✨ READY TO USE  
**Gateway:** http://localhost:5000  
**Coordinator:** http://localhost:8000  
**Testing:** ./test_whatsapp_integration.sh  
**Production:** Follow Twilio setup above  

Your JARVIS is listening on WhatsApp! 🤖📱
