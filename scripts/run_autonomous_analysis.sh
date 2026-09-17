#!/bin/bash
# Autonome n8n-Analyse und Qdrant-Konfiguration
# Läuft auf dem lokalen Server mit Zugriff auf n8n (localhost:3000)

set -e

cd "$(dirname "$0")/.."

echo "🤖 Starte AUTONOME n8n-Analyse und Qdrant-Konfiguration"
echo "=========================================="

# Lade .env für n8n API-Key
if [ -f .env ]; then
    set -a
    source .env
    set +a
    echo "✅ .env geladen"
else
    echo "❌ .env nicht gefunden"
    exit 1
fi

# 1. Analysiere n8n Infrastructure
echo ""
echo "📊 Schritt 1: Analysiere n8n-Infrastruktur..."
python3 plugins/n8n_analyzer.py --action analyze > /tmp/n8n_analysis.log 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Analyse abgeschlossen"
else
    echo "⚠️ Analyse-Fehler (siehe /tmp/n8n_analysis.log)"
fi

# 2. Erstelle/Verifiziere Qdrant-Credential
echo ""
echo "⚙️ Schritt 2: Konfiguriere Qdrant-Credential..."
python3 scripts/setup_qdrant_credential.py \
    --n8n-url "http://localhost:3000" \
    --qdrant-host "http://172.17.0.1" > /tmp/qdrant_setup.log 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Qdrant-Credential konfiguriert"
else
    echo "⚠️ Credential-Setup fehlgeschlagen (siehe /tmp/qdrant_setup.log)"
fi

# 3. Führe Handler aus
echo ""
echo "🔗 Schritt 3: Führe n8n Connector Handler aus..."
python3 scripts/n8n_connector_handler.py \
    --n8n-url "http://localhost:3000" \
    --api-key "$N8N_API_KEY" > /tmp/connector_handler.log 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Connector Handler abgeschlossen"
    if [ -f n8n_status.json ]; then
        echo ""
        echo "📋 n8n Status Report:"
        python3 -m json.tool n8n_status.json | head -30
    fi
else
    echo "⚠️ Connector Handler fehlgeschlagen (siehe /tmp/connector_handler.log)"
fi

echo ""
echo "=========================================="
echo "✨ Autonome Analyse ABGESCHLOSSEN"
echo "=========================================="
echo ""
echo "📁 Ergebnisse:"
[ -f n8n_analysis_report.json ] && echo "   - n8n_analysis_report.json"
[ -f n8n_status.json ] && echo "   - n8n_status.json"
[ -f orchestration_results.json ] && echo "   - orchestration_results.json"

echo ""
echo "🚀 Nächste Schritte:"
echo "1. Verifiziere Ergebnisse in den JSON-Dateien"
echo "2. Aktiviere identifizierte Qdrant-Workflows in n8n UI"
echo "3. Teste Qdrant-Verbindung mit einem Test-Workflow"
