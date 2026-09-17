#!/bin/bash
# Complete Orchestrator Setup & Run Script
# Copy and run this entire script

set -e

# Use current directory instead of hardcoded path
PROJECT_DIR="$(pwd)"

echo "🚀 Claude ↔ OpenAI Orchestrator Setup & Run"
echo "=========================================="

# Step 1: Pull latest scripts
echo "📥 Pulling latest scripts from GitHub..."
git pull origin claude/session-01a0ae45-continuation-lmlahz 2>/dev/null || echo "⚠️  Git pull skipped"

# Step 2: Get API Keys
echo ""
echo "🔑 API Key Setup"
echo "=========================================="
echo ""

read -sp "Enter your NEW OpenAI API Key (sk-...): " OPENAI_KEY
echo ""
read -sp "Enter your n8n API Key: " N8N_KEY
echo ""

# Step 3: Create .env file
echo "💾 Creating .env file..."
cat > .env << EOF
OPENAI_API_KEY=$OPENAI_KEY
N8N_API_KEY=$N8N_KEY
EOF

chmod 600 .env
echo "✅ .env created (secure, not tracked by git)"

# Step 4: Verify scripts exist
echo ""
echo "🔍 Verifying scripts..."
for script in autonomous_openai_agent.py n8n_connector_handler.py auto_upload_results.py claude_openai_orchestrator.py; do
    if [ -f "scripts/$script" ]; then
        echo "   ✅ $script found"
    else
        echo "   ❌ $script NOT found - run 'git pull' first"
        exit 1
    fi
done

# Step 5: Install dependencies
echo ""
echo "📦 Checking Python dependencies..."
python3 -c "import openai" 2>/dev/null && echo "   ✅ openai installed" || {
    echo "   📥 Installing openai..."
    pip install openai -q
}
python3 -c "import requests" 2>/dev/null && echo "   ✅ requests installed" || {
    echo "   📥 Installing requests..."
    pip install requests -q
}

# Step 6: Run Orchestrator
echo ""
echo "🤖 Starting Claude ↔ OpenAI Orchestrator..."
echo "=========================================="
echo ""

source .env
python3 scripts/claude_openai_orchestrator.py --all

# Step 7: Upload results
echo ""
echo "📤 Uploading results..."
python3 scripts/auto_upload_results.py

echo ""
echo "✨ ORCHESTRATION COMPLETE!"
echo "=========================================="
