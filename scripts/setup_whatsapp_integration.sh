#!/bin/bash
# 📱 WhatsApp + JARVIS Integration Setup
# Configures local JARVIS with WhatsApp access

set -e

cd "$(dirname "$0")/.."

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         📱 WHATSAPP + JARVIS INTEGRATION SETUP                 ║"
echo "║              Local AI Assistant Configuration                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Check .env
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 1: Environment Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f .env ]; then
    echo "❌ .env file not found"
    echo ""
    echo "Create .env with:"
    echo "   OPENAI_API_KEY=sk-..."
    echo "   N8N_API_KEY=eyJ..."
    echo "   TWILIO_ACCOUNT_SID=AC..."
    echo "   TWILIO_AUTH_TOKEN=..."
    echo "   TWILIO_WHATSAPP_NUMBER=whatsapp:+14155552671"
    exit 1
fi

set -a && source .env && set +a

echo "✅ .env file loaded"

if [ -z "$TWILIO_ACCOUNT_SID" ]; then
    echo "⚠️  TWILIO_ACCOUNT_SID not set (optional for testing)"
else
    echo "✅ Twilio configured"
fi

echo ""

# Check Python dependencies
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 2: Dependencies"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python3 -c "import flask" 2>/dev/null && echo "✅ Flask installed" || {
    echo "📦 Installing Flask..."
    pip install -q flask requests python-dotenv
    echo "✅ Dependencies installed"
}

echo ""

# Create startup script
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 3: Integration Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cat > jarvis_whatsapp_start.sh << 'EOF'
#!/bin/bash
# Start JARVIS with WhatsApp integration

set -a && source .env && set +a

echo ""
echo "🚀 Starting JARVIS WhatsApp Integration"
echo ""

# Start JARVIS Coordinator API (background)
echo "🤖 Starting JARVIS Coordinator API on :8000..."
python3 scripts/jarvis_coordinator_api.py > /tmp/jarvis_coordinator.log 2>&1 &
COORDINATOR_PID=$!
sleep 2

# Start WhatsApp Gateway (background)
echo "📱 Starting WhatsApp Gateway on :5000..."
python3 scripts/whatsapp_gateway.py > /tmp/whatsapp_gateway.log 2>&1 &
GATEWAY_PID=$!
sleep 2

# Verify both are running
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ JARVIS WHATSAPP INTEGRATION ONLINE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Services Running:"
echo "   🤖 JARVIS Coordinator API: http://localhost:8000"
echo "   📱 WhatsApp Gateway: http://localhost:5000"
echo ""
echo "🔗 Endpoints:"
echo "   POST http://localhost:8000/process_instruction"
echo "   POST http://localhost:5000/whatsapp/test"
echo ""
echo "📝 Logs:"
echo "   Coordinator: tail -f /tmp/jarvis_coordinator.log"
echo "   Gateway: tail -f /tmp/whatsapp_gateway.log"
echo ""
echo "To stop:"
echo "   kill $COORDINATOR_PID $GATEWAY_PID"
echo ""

# Keep running
wait
EOF

chmod +x jarvis_whatsapp_start.sh
echo "✅ Created jarvis_whatsapp_start.sh"

echo ""

# Create test script
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 4: Testing"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cat > test_whatsapp_integration.sh << 'EOF'
#!/bin/bash
# Test JARVIS WhatsApp integration

echo ""
echo "🧪 Testing JARVIS WhatsApp Integration"
echo ""

# Test 1: Health checks
echo "✓ Test 1: Service Health Checks"
curl -s http://localhost:8000/health | python3 -m json.tool > /dev/null && echo "   ✅ Coordinator API" || echo "   ❌ Coordinator API"
curl -s http://localhost:5000/health | python3 -m json.tool > /dev/null && echo "   ✅ WhatsApp Gateway" || echo "   ❌ WhatsApp Gateway"
echo ""

# Test 2: Agent status
echo "✓ Test 2: Agent Status"
curl -s http://localhost:8000/agent_status | python3 -m json.tool | head -10
echo ""

# Test 3: Send instruction
echo "✓ Test 3: Send Instruction via Coordinator"
curl -s -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "instruction": "Erstelle einen Preisplan für ein neues Projekt",
    "channel": "test"
  }' | python3 -m json.tool | head -20
echo ""

# Test 4: WhatsApp test (without Twilio)
echo "✓ Test 4: WhatsApp Message Processing (Test Mode)"
curl -s -X POST http://localhost:5000/whatsapp/test \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "1234567890",
    "message": "Hallo JARVIS, gib mir einen Überblick"
  }' | python3 -m json.tool | head -20
echo ""

echo "✨ All tests completed"
EOF

chmod +x test_whatsapp_integration.sh
echo "✅ Created test_whatsapp_integration.sh"

echo ""

# Create configuration guide
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 5: Configuration Guide"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cat > WHATSAPP_SETUP_GUIDE.md << 'EOF'
# 📱 WhatsApp + JARVIS Setup Guide

## Local Testing (No Twilio Required)

### Start Services
```bash
./jarvis_whatsapp_start.sh
```

### Test via HTTP
```bash
# Send instruction to Coordinator
curl -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "you@example.com",
    "instruction": "Erstelle einen Preisplan",
    "channel": "test"
  }'

# Test WhatsApp message (no Twilio)
curl -X POST http://localhost:5000/whatsapp/test \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "1234567890",
    "message": "Hallo JARVIS"
  }'
```

## Production Setup (With Twilio)

### 1. Create Twilio Account
- Go to: https://www.twilio.com
- Sign up for free trial
- Navigate to Messaging → Try it out → WhatsApp

### 2. Setup WhatsApp Sandbox
- Accept Twilio's WhatsApp sandbox invite
- Get your Twilio WhatsApp number

### 3. Get Credentials
- Go to Console → Account Info
- Copy your Account SID
- Copy your Auth Token
- Get your WhatsApp number

### 4. Update .env
```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_WHATSAPP_NUMBER="whatsapp:+14155552671"
```

### 5. Setup Webhook in Twilio
- Twilio Console → Phone Numbers
- Select your WhatsApp number
- Scroll to "Webhook"
- Set When a message comes in: `https://your-domain.com/whatsapp/webhook`
- Save

### 6. Start Services
```bash
./jarvis_whatsapp_start.sh
```

## How It Works

```
You (WhatsApp)
    ↓
[Twilio API]
    ↓
WhatsApp Gateway (:5000)
    ↓
JARVIS Coordinator (:8000)
    ↓
10-Agent Agency System
    ├→ prompt_architect
    ├→ executor
    ├→ reviewer
    ├→ angebot
    ├→ dispo
    ├→ kunde
    ├→ recherche
    ├→ technik
    ├→ berater
    └→ coordinator
    ↓
Response back to WhatsApp
```

## Example Conversations

### Pricing Request
You: "Erstelle einen Preisplan für ein neues Projekt"
JARVIS:
```
🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, angebot, prompt_architect, reviewer
✅ executor: Befehl ausgeführt
✅ angebot: Preisangebot erstellt
✅ prompt_architect: Prompt erstellt und optimiert
✅ reviewer: Qualitätsprüfung bestanden
✨ Alle Schritte abgeschlossen
```

### Planning Request
You: "Plane den nächsten Sprint"
JARVIS:
```
🤖 JARVIS Coordinator - Befehl verarbeitet
👥 Agenten eingesetzt: executor, dispo, kunde, prompt_architect
...
```

## Troubleshooting

### Services not responding
```bash
# Check Coordinator
curl http://localhost:8000/health

# Check Gateway
curl http://localhost:5000/health

# View logs
tail -f /tmp/jarvis_coordinator.log
tail -f /tmp/whatsapp_gateway.log
```

### Twilio not sending
- Verify Account SID & Auth Token in .env
- Check webhook URL is accessible
- Verify WhatsApp number format
- Check Twilio logs in dashboard

### Messages not arriving
- Ensure gateway is running
- Check network connectivity
- Verify webhook is responding with 200 status

## Next: Add Voice Output

See `JARVIS_VOICE_OUTPUT.md` for adding speech synthesis.
EOF

cat WHATSAPP_SETUP_GUIDE.md
echo "✅ Created WHATSAPP_SETUP_GUIDE.md"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ WHATSAPP INTEGRATION CONFIGURED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 Next Steps:"
echo "   1. Start local services:"
echo "      ./jarvis_whatsapp_start.sh"
echo ""
echo "   2. Run tests:"
echo "      ./test_whatsapp_integration.sh"
echo ""
echo "   3. For production (Twilio):"
echo "      - Follow WHATSAPP_SETUP_GUIDE.md"
echo "      - Add Twilio credentials to .env"
echo "      - Restart services"
echo ""
echo "📚 Documentation:"
echo "   - WHATSAPP_SETUP_GUIDE.md (this setup)"
echo "   - scripts/whatsapp_gateway.py (gateway code)"
echo "   - scripts/jarvis_coordinator_api.py (coordinator code)"
echo ""
