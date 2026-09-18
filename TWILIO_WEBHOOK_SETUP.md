# 🔗 Twilio Webhook Configuration

Nach der Konfiguration der Credentials musst du noch die **Webhook URL** in Twilio eintragen.

---

## 📍 Webhook URL

Deine JARVIS WhatsApp Gateway läuft auf:
```
http://YOUR_DOMAIN_OR_IP:5000/whatsapp/webhook
```

### Beispiele:
```
Local/Testing:     http://localhost:5000/whatsapp/webhook
Cloud/Production:  https://your-domain.com/whatsapp/webhook
```

---

## ⚙️ Twilio Console Setup

1. Öffne: https://www.twilio.com/console/messaging/whatsapp/learn
2. Gehe zu **WhatsApp Sandbox Settings**
3. Unter **Webhook URLs**, füge ein:

**When a message comes in:**
```
http://YOUR_DOMAIN:5000/whatsapp/webhook
POST
```

**Status Callbacks:**
```
http://YOUR_DOMAIN:5000/whatsapp/webhook
POST
```

4. **Save**

---

## 🧪 Test-Webhook (LOCAL)

Falls du lokal testest (kein Public Domain):

```bash
# Terminal 1: Start JARVIS services
python3 scripts/jarvis_coordinator_api.py &
python3 scripts/whatsapp_gateway_with_voice.py &

# Terminal 2: Test webhook (no Twilio needed)
curl -X POST http://localhost:5000/whatsapp/test \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "+49123456789",
    "message": "Hallo JARVIS",
    "voice": true
  }'
```

---

## 🌐 Production Webhook (PUBLIC)

Für echte WhatsApp Messages brauchst du eine **öffentliche URL**:

### Option 1: ngrok (schnell & kostenlos)
```bash
# Terminal 1: Start JARVIS
python3 scripts/whatsapp_gateway_with_voice.py &

# Terminal 2: Create public tunnel
ngrok http 5000
# Output: https://abc123.ngrok.io
# → Use: https://abc123.ngrok.io/whatsapp/webhook in Twilio
```

### Option 2: Cloud Deployment (z.B. Heroku free tier)
Deploy JARVIS zu Cloud → erhältst public URL → trage in Twilio ein

### Option 3: Own Domain
Wenn du eigenen Server hast → https://your-domain.com:5000/whatsapp/webhook

---

## 🔄 Message Flow

```
WhatsApp User sends message
    ↓
Twilio empfängt
    ↓
POST to your webhook: /whatsapp/webhook
    ↓
JARVIS Gateway processes
    ↓
Coordinator routes to agents
    ↓
Response generated + Voice created
    ↓
Twilio sendet Text + Voice zurück
    ↓
User empfängt auf WhatsApp
```

---

## ✅ Webhook Test Checklist

- [ ] Credentials konfiguriert
- [ ] JARVIS Gateway läuft (port 5000)
- [ ] JARVIS Coordinator läuft (port 8000)
- [ ] Webhook URL in Twilio eingetragen
- [ ] Test-Nachricht via WhatsApp gesendet
- [ ] Response mit Voice erhalten ✅

---

## 🐛 Troubleshooting

**Webhook wird nicht aufgerufen:**
- [ ] Firewall: Port 5000 offen?
- [ ] URL öffentlich erreichbar? (ngrok/domain)
- [ ] Twilio Webhook URL korrekt gespeichert?
- [ ] WhatsApp Sandbox aktiv?

**Messages kommen an aber keine Response:**
- [ ] Coordinator läuft? → `curl http://localhost:8000/health`
- [ ] Logs anschauen → `tail -f /tmp/gateway.log`
- [ ] .env richtig konfiguriert?

---

**Sobald alles läuft:** JARVIS antwortet via WhatsApp + Voice! 🎉🎙️
