#!/bin/bash
# Start JARVIS with WhatsApp integration

cd "$(dirname "$0")"

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
