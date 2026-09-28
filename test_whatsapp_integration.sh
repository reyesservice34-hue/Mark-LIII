#!/bin/bash
# Test JARVIS WhatsApp integration

echo ""
echo "🧪 Testing JARVIS WhatsApp Integration"
echo ""

# Test 1: Health checks
echo "✓ Test 1: Service Health Checks"
curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool > /dev/null && echo "   ✅ Coordinator API" || echo "   ❌ Coordinator API (not running)"
curl -s http://localhost:5000/health 2>/dev/null | python3 -m json.tool > /dev/null && echo "   ✅ WhatsApp Gateway" || echo "   ❌ WhatsApp Gateway (not running)"
echo ""

# Test 2: Agent status
echo "✓ Test 2: Agent Status"
echo "   Fetching agent status..."
curl -s http://localhost:8000/agent_status 2>/dev/null | python3 -m json.tool | head -15 || echo "   ⚠️  Could not fetch agent status (service not running)"
echo ""

# Test 3: Send instruction
echo "✓ Test 3: Send Instruction via Coordinator"
echo "   Sending test instruction..."
curl -s -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "instruction": "Erstelle einen Preisplan für ein neues Projekt",
    "channel": "test"
  }' 2>/dev/null | python3 -m json.tool | head -25 || echo "   ⚠️  Could not send instruction (service not running)"
echo ""

# Test 4: WhatsApp test (without Twilio)
echo "✓ Test 4: WhatsApp Message Processing (Test Mode)"
echo "   Processing test message..."
curl -s -X POST http://localhost:5000/whatsapp/test \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "1234567890",
    "message": "Hallo JARVIS, gib mir einen Überblick"
  }' 2>/dev/null | python3 -m json.tool | head -25 || echo "   ⚠️  Could not process message (service not running)"
echo ""

echo "✨ Testing completed"
echo ""
echo "📝 To start services, run:"
echo "   ./jarvis_whatsapp_start.sh"
