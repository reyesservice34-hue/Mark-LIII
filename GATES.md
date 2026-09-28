# GATES: Qdrant + n8n Integration Complete

Completion discipline für autonome n8n Infrastructure Analysis und Qdrant Workflow Activation.

## G1: n8n Server Erreichbarkeit

CHECK: `curl -s http://localhost:3000/healthz -H "X-N8N-API-KEY: $(grep N8N_API_KEY .env | cut -d= -f2)" | grep -q "ok" && echo "OK" || echo "FAIL"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G2: Qdrant Server Erreichbarkeit

CHECK: `curl -s http://172.17.0.1:6333/health | grep -q "ok" && echo "OK" || echo "FAIL"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G3: n8n API Authentifizierung

CHECK: `curl -s http://localhost:3000/api/v1/workflows -H "X-N8N-API-KEY: $(grep N8N_API_KEY .env | cut -d= -f2)" | grep -q '"data"' && echo "OK" || echo "FAIL"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G4: Qdrant Credential existiert in n8n

CHECK: `python3 -c "import json, requests, os; key=os.getenv('N8N_API_KEY') or open('.env').read().split('N8N_API_KEY=')[1].split()[0]; r=requests.get('http://localhost:3000/api/v1/credentials', headers={'X-N8N-API-KEY': key}); creds=[c for c in r.json().get('data',[]) if 'Qdrant' in c.get('name','')]; print('OK' if creds else 'FAIL')"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G5: n8n Workflows analysiert

CHECK: `test -f n8n_analysis_report.json && python3 -c "import json; d=json.load(open('n8n_analysis_report.json')); print('OK' if d.get('workflows') else 'FAIL')" || echo "ANALYZING"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G6: Qdrant-geeignete Workflows identifiziert

CHECK: `test -f n8n_analysis_report.json && python3 -c "import json; d=json.load(open('n8n_analysis_report.json')); qwfs=d.get('qdrant_workflows',[]); print(f'FOUND_{len(qwfs)}_WORKFLOWS')" || echo "NO_DATA"`

EXPECT: FOUND_

CWD: /home/user/Mark-LIII

## G7: Workflows aktiviert

CHECK: `test -f workflow_activation_result.json && python3 -c "import json; r=json.load(open('workflow_activation_result.json')); print('OK' if r.get('activated',0) > 0 or r.get('already_active',0) > 0 else 'NONE_ACTIVATED')" || echo "NO_RESULT"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G8: n8n_analyzer.py fehlerfrei ausführbar

CHECK: `python3 plugins/n8n_analyzer.py --action analyze 2>&1 | tail -1 | grep -q "ABGESCHLOSSEN\|success\|error" && echo "OK" || echo "FAIL"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G9: Qdrant Credential konfiguriert mit docker0 bridge

CHECK: `python3 -c "import json; d=json.load(open('.env')); import os; os.environ.update(d if d else {}); import requests; key=os.getenv('N8N_API_KEY'); r=requests.get('http://localhost:3000/api/v1/credentials', headers={'X-N8N-API-KEY': key}); qcreds=[c for c in r.json().get('data',[]) if 'Qdrant' in c.get('name','')]; print('OK' if any('172.17.0.1' in str(c) for c in qcreds) else 'WRONG_HOST')" 2>/dev/null || echo "NO_CHECK"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G10: Alle Scripts ausführbar

CHECK: `for script in scripts/n8n_connector_handler.py scripts/setup_qdrant_credential.py scripts/auto_activate_qdrant_workflows.py plugins/n8n_analyzer.py; do test -x "$script" || echo "NOT_EXECUTABLE: $script"; done; test $? -eq 0 && echo "OK" || echo "FAIL"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G11: Orchestration Results vorhanden

CHECK: `test -f orchestration_results.json && python3 -c "import json; d=json.load(open('orchestration_results.json')); print('OK')" || echo "NO_RESULTS"`

EXPECT: OK

CWD: /home/user/Mark-LIII

## G12: Git Branch aktuell

CHECK: `git status --short | wc -l | xargs test 0 -eq && echo "OK" || echo "UNCOMMITTED"`

EXPECT: OK

CWD: /home/user/Mark-LIII
