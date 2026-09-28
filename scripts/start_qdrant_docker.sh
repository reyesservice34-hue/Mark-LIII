#!/bin/bash
# Autonomous Qdrant Docker Startup
# Startet Qdrant vollautomatisch mit Collections-Initialisierung

set -e

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "🚀 QDRANT AUTONOMOUS STARTUP"
echo "════════════════════════════════════════════════════════════════════════"
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed"
    echo "   Install from: https://www.docker.com/products/docker-desktop"
    exit 1
fi

echo "✅ Docker found"

# Check if Qdrant container is already running
if docker ps | grep -q qdrant; then
    echo "✅ Qdrant container already running"
    QDRANT_RUNNING=true
else
    QDRANT_RUNNING=false
fi

# Check if Qdrant container exists but is stopped
if docker ps -a | grep -q qdrant; then
    echo "🔄 Starting existing Qdrant container..."
    docker start $(docker ps -a | grep qdrant | awk '{print $1}') 2>/dev/null || true
    sleep 2
else
    if [ "$QDRANT_RUNNING" = false ]; then
        echo "🔄 Starting new Qdrant Docker container..."
        docker run -d \
            --name qdrant-mark-liii \
            -p 6333:6333 \
            -v qdrant_storage:/qdrant/storage \
            qdrant/qdrant:latest

        echo "⏳ Waiting for Qdrant to start..."
        sleep 3
    fi
fi

# Verify Qdrant is running
echo "🔍 Verifying Qdrant connection..."
MAX_ATTEMPTS=5
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if curl -s http://localhost:6333/health > /dev/null 2>&1; then
        echo "✅ Qdrant is running on localhost:6333"
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    if [ $ATTEMPT -lt $MAX_ATTEMPTS ]; then
        echo "⏳ Waiting... (attempt $ATTEMPT/$MAX_ATTEMPTS)"
        sleep 2
    fi
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo "❌ Qdrant failed to start"
    exit 1
fi

# Initialize collections
echo ""
echo "📁 Initializing Qdrant Collections..."
echo ""

cd /home/user/Mark-LIII

python3 scripts/init_qdrant_collections.py

# Verify collections
echo ""
echo "✅ Verifying collections..."

COLLECTIONS=$(curl -s http://localhost:6333/collections | grep -o '"name":"[^"]*"' | wc -l)
echo "   Found $COLLECTIONS collections"

# Show status
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✨ QDRANT READY FOR n8n!"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "📊 Collections created:"
echo "   ✅ documents (semantic search)"
echo "   ✅ document_classes (classification)"
echo "   ✅ conversation_history (context)"
echo ""
echo "🔗 Connection details:"
echo "   URL: http://localhost:6333"
echo "   Status: READY"
echo ""
echo "📱 Next steps:"
echo "   1. Configure n8n credential (HTTP → localhost:6333)"
echo "   2. Create Qdrant workflow nodes"
echo "   3. Test with embeddings"
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo ""

exit 0
