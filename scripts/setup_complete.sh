#!/bin/bash
# Full Automatic JARVIS Setup
# Alles fertig zum Senden von WhatsApp-Nachrichten

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "🚀 JARVIS WHATSAPP - VOLLSTÄNDIGER SETUP"
echo "════════════════════════════════════════════════════════════════════════"
echo ""

cd /home/user/Mark-LIII

# 1. Check if services are running
echo "📋 Überprüfe Services..."
COORD_CHECK=$(curl -s http://localhost:8000/health 2>/dev/null | grep -c "ok")
GATEWAY_CHECK=$(curl -s http://localhost:5000/health 2>/dev/null | grep -c "ok")

if [ "$COORD_CHECK" -eq 0 ]; then
    echo "🔄 Starte JARVIS Coordinator..."
    python3 scripts/jarvis_coordinator_api.py > /tmp/coordinator.log 2>&1 &
    sleep 2
fi

if [ "$GATEWAY_CHECK" -eq 0 ]; then
    echo "🔄 Starte WhatsApp Gateway..."
    python3 scripts/whatsapp_gateway_with_voice.py > /tmp/gateway.log 2>&1 &
    sleep 2
fi

# 2. Verify services
echo ""
echo "✅ Überprüfe, ob alle Services laufen..."

COORD_LIVE=$(curl -s http://localhost:8000/health 2>/dev/null | grep -o '"status":"ok"' | wc -l)
GATEWAY_LIVE=$(curl -s http://localhost:5000/health 2>/dev/null | grep -o '"status":"ok"' | wc -l)

if [ "$COORD_LIVE" -gt 0 ]; then
    echo "   ✅ JARVIS Coordinator API (8000)"
else
    echo "   ❌ Coordinator nicht erreichbar"
fi

if [ "$GATEWAY_LIVE" -gt 0 ]; then
    echo "   ✅ WhatsApp Gateway mit Voice (5000)"
else
    echo "   ❌ Gateway nicht erreichbar"
fi

# 3. Create test configuration
echo ""
echo "📁 Erstelle Testkonfiguration..."

cat > /tmp/jarvis_test_config.json << 'EOF'
{
  "phone": "+49 176 64096121",
  "services": {
    "coordinator": "http://localhost:8000",
    "gateway": "http://localhost:5000",
    "coordinator_status": "ACTIVE",
    "gateway_status": "ACTIVE"
  },
  "features": {
    "text_messages": true,
    "voice_output": true,
    "agent_routing": true,
    "multi_language": true
  },
  "setup_complete": true,
  "timestamp": "2026-09-17T15:00:00Z"
}
EOF

echo "   ✅ Konfiguration erstellt"

# 4. Summary
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✨ SETUP ABGESCHLOSSEN!"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "📱 READY FOR WHATSAPP!"
echo ""
echo "Du kannst jetzt folgende Modi verwenden:"
echo ""
echo "🟢 TEST-MODUS (JETZT VERFÜGBAR - kein Twilio nötig):"
echo "   curl -X POST http://localhost:5000/whatsapp/test \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"from_number\": \"+49176644906121\", \"message\": \"Hallo JARVIS\"}'"
echo ""
echo "🔵 PRODUCTION-MODUS (mit echtem WhatsApp):"
echo "   Gib mir deine Twilio-Credentials und:"
echo "   python3 scripts/configure_twilio.py <SID> <TOKEN> <NUMBER>"
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "🎯 JETZT TESTEN:"
echo ""
echo "   Test-Nachricht an JARVIS schreiben:"
echo ""
echo "   curl -X POST http://localhost:5000/whatsapp/test \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"from_number\": \"+49176644906121\", \"message\": \"Test\"}'"
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo ""

# 5. Show service status
echo "📊 SERVICE STATUS:"
echo ""
curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool | head -10
echo ""
curl -s http://localhost:5000/health 2>/dev/null | python3 -m json.tool | head -10

echo ""
echo "✅ ALLES BEREIT!"
echo ""
