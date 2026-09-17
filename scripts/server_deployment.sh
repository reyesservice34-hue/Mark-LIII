#!/bin/bash
# Server-Side Deployment & Testing
# Run this on your local server (jarvis_n8n machine)
# This validates everything works on your infrastructure

set -e

cd "$(dirname "$0")/.."

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "🚀 SERVER-SIDE DEPLOYMENT & VALIDATION"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Load environment
if [ ! -f .env ]; then
    echo "❌ .env file not found"
    exit 1
fi

set -a
source .env
set +a

echo "✅ Environment loaded"
echo ""

# ============================================================
# TEST 1: Qdrant Collection Initialization
# ============================================================
echo "📦 TEST 1: Qdrant Collection Initialization"
echo "────────────────────────────────────────────────────────────────"

python3 scripts/init_qdrant_collections.py

if [ $? -eq 0 ]; then
    echo "✅ TEST 1 PASSED: Qdrant collections initialized"
    TEST1_PASSED=true
else
    echo "❌ TEST 1 FAILED: Qdrant initialization error"
    TEST1_PASSED=false
fi

echo ""

# ============================================================
# TEST 2: n8n Workflow Deployment
# ============================================================
echo "🔄 TEST 2: n8n Workflow Deployment"
echo "────────────────────────────────────────────────────────────────"

python3 scripts/deploy_n8n_workflows.py

if [ $? -eq 0 ]; then
    echo "✅ TEST 2 PASSED: Workflows deployed to n8n"
    TEST2_PASSED=true
else
    echo "❌ TEST 2 FAILED: Workflow deployment error"
    TEST2_PASSED=false
fi

echo ""

# ============================================================
# TEST 3: Complete Orchestration
# ============================================================
echo "🎯 TEST 3: Complete Orchestration Run"
echo "────────────────────────────────────────────────────────────────"

bash scripts/autonomous_complete.sh

if [ $? -eq 0 ]; then
    echo "✅ TEST 3 PASSED: Full orchestration successful"
    TEST3_PASSED=true
else
    echo "❌ TEST 3 FAILED: Orchestration error"
    TEST3_PASSED=false
fi

echo ""

# ============================================================
# RESULTS SUMMARY
# ============================================================
echo "════════════════════════════════════════════════════════════════"
echo "📋 DEPLOYMENT RESULTS"
echo "════════════════════════════════════════════════════════════════"
echo ""

PASSED=0
TOTAL=3

[ "$TEST1_PASSED" = true ] && PASSED=$((PASSED + 1)) && echo "✅ Test 1: Qdrant initialization - PASSED" || echo "❌ Test 1: Qdrant initialization - FAILED"
[ "$TEST2_PASSED" = true ] && PASSED=$((PASSED + 1)) && echo "✅ Test 2: n8n deployment - PASSED" || echo "❌ Test 2: n8n deployment - FAILED"
[ "$TEST3_PASSED" = true ] && PASSED=$((PASSED + 1)) && echo "✅ Test 3: Full orchestration - PASSED" || echo "❌ Test 3: Full orchestration - FAILED"

echo ""
echo "Summary: $PASSED/$TOTAL tests passed"
echo ""

# ============================================================
# VERIFICATION CHECKS
# ============================================================
echo "🔍 VERIFICATION CHECKS"
echo "────────────────────────────────────────────────────────────────"

# Check Qdrant collections
echo "Checking Qdrant collections..."
python3 -c "
import requests
try:
    r = requests.get('http://172.17.0.1:6333/collections', timeout=5)
    collections = r.json().get('result', {}).get('collections', [])
    for col in collections:
        print(f\"  ✅ Collection: {col.get('name')}\")
except Exception as e:
    print(f\"  ⚠️  Could not verify collections: {e}\")
" || true

echo ""

# Check n8n workflows
echo "Checking n8n workflows..."
python3 -c "
import requests
import os
headers = {'X-N8N-API-KEY': os.getenv('N8N_API_KEY', '')}
try:
    r = requests.get('http://localhost:3000/api/v1/workflows', headers=headers, timeout=5)
    workflows = r.json().get('data', [])
    print(f\"  Found {len(workflows)} workflows\")
    for wf in workflows[-2:]:
        print(f\"  - {wf.get('name')} (ID: {wf.get('id')})\")
except Exception as e:
    print(f\"  ⚠️  Could not verify workflows: {e}\")
" || true

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✨ SERVER DEPLOYMENT COMPLETE"
echo "════════════════════════════════════════════════════════════════"
echo ""

if [ $PASSED -eq $TOTAL ]; then
    echo "🎉 ALL TESTS PASSED - SYSTEM READY FOR JARVIS"
    echo ""
    echo "Next: Initialize JARVIS on your server"
    exit 0
else
    echo "⚠️  Some tests failed - Review logs above"
    exit 1
fi
