# 🚀 SERVER DEPLOYMENT GUIDE
## Deploy & Validate JARVIS Infrastructure on Your Server

**Duration:** ~10-15 minutes  
**Prerequisites:** Docker running, n8n + Qdrant online  

---

## QUICK START (3 Commands)

```bash
# 1. Navigate to project
cd ~/Mark-LIII

# 2. Pull latest from GitHub
git pull origin claude/session-01a0ae45-continuation-lmlahz

# 3. Run deployment (all tests automated)
bash scripts/server_deployment.sh
```

**Done!** Script will:
- ✅ Initialize Qdrant collections
- ✅ Deploy workflows to n8n
- ✅ Run full orchestration
- ✅ Verify everything works
- ✅ Report results

---

## WHAT HAPPENS STEP-BY-STEP

### Phase 1: Qdrant Collections (2-3 min)
```
Actions:
- Creates: documents collection (1536-dim vectors)
- Creates: document_classes collection
- Creates: search_cache collection
- Configures: payload indexes

Expected Output:
✅ Qdrant (172.17.0.1:6333): Reachable
✅ Collections created successfully
✅ Payload indexes configured
```

### Phase 2: n8n Workflow Deployment (2-3 min)
```
Actions:
- Imports: Semantic Search Pipeline
- Imports: Document Classification workflow
- Activates: Semantic Search (for testing)
- Verifies: Workflows in n8n UI

Expected Output:
✅ n8n (localhost:3000): Reachable
✅ 2 workflows deployed
✅ Workflows visible in n8n
```

### Phase 3: Full Orchestration (3-5 min)
```
Actions:
- Analyzes n8n infrastructure
- Configures Qdrant credentials
- Activates eligible workflows
- Validates completion gates

Expected Output:
✅ Analysis complete: 5 workflows
✅ Qdrant credential configured
✅ Workflows activated
✅ Gates verified
```

### Phase 4: Verification (1-2 min)
```
Actions:
- Lists Qdrant collections
- Lists n8n workflows
- Shows deployment status

Expected Output:
✅ Collections: documents, document_classes, search_cache
✅ Workflows: Semantic Search Pipeline, Document Classification
✅ Status: READY FOR JARVIS
```

---

## IF SOMETHING FAILS

### Qdrant Connection Error
```
Error: "Connection to 172.17.0.1 timed out"

Solutions:
1. Check Qdrant is running: docker ps | grep qdrant
2. Verify bridge IP: docker network inspect bridge | grep Gateway
3. Ensure Qdrant on port 6333: docker port <qdrant_container> 6333
```

### n8n Connection Error
```
Error: "Failed to establish connection to localhost:3000"

Solutions:
1. Check n8n running: docker ps | grep n8n
2. Verify port mapping: docker port <n8n_container> 3000
3. Check API key in .env: grep N8N_API_KEY .env
```

### Workflow Deployment Fails
```
Error: "Failed to create workflow: 409" (conflict)

Solutions:
1. Workflow already exists (OK, skips)
2. Invalid JSON (check workflow file: cat qdrant_semantic_search_workflow.json)
3. n8n API key invalid (refresh key in .env)
```

---

## AFTER SUCCESSFUL DEPLOYMENT

### Verify in n8n UI
1. Open http://localhost:3000
2. Go to Workflows
3. Should see:
   - ✅ Semantic Search Pipeline (new)
   - ✅ Document Classification (new)
   - ✓ Other existing workflows

### Verify in Qdrant UI (if available)
1. Open Qdrant UI (if running)
2. Should see collections:
   - ✅ documents
   - ✅ document_classes
   - ✅ search_cache

### Check Logs
```bash
# See deployment results
cat n8n_deployment_results.json | python3 -m json.tool

# See Qdrant init results
cat qdrant_collections_init_results.json | python3 -m json.tool

# See orchestration results
cat orchestration_results.json | python3 -m json.tool
```

---

## NEXT PHASE: JARVIS LAUNCH

Once all tests pass with ✅:

```bash
# 1. Verify system health from GitHub
# (System audit already completed in cloud)

# 2. Initialize JARVIS Coordinator
bash scripts/jarvis_init.sh

# 3. Activate Agency (10 Agents)
python3 scripts/activate_agency.py

# 4. Start autonomous loops
python3 scripts/start_autonomous_loops.py
```

---

## TROUBLESHOOTING CHECKLIST

```
Before running deployment:
[ ] .env file exists and has keys
[ ] Docker daemon running
[ ] n8n container running
[ ] Qdrant container running
[ ] Network connectivity OK

During deployment:
[ ] Watch for errors in output
[ ] Check timestamps (should complete in 15 min)
[ ] Verify gates pass (3+/4)

After deployment:
[ ] Check results JSONs exist
[ ] Verify Qdrant collections created
[ ] Verify n8n workflows appear
[ ] Check git status (should be clean)
```

---

## SUCCESS CRITERIA

✅ **Deployment is successful if:**
1. Qdrant test passes (collections created)
2. n8n test passes (workflows deployed)
3. Orchestration test passes (gates verified)
4. Verification shows all components

✅ **You should see:**
```
✨ SERVER DEPLOYMENT COMPLETE
🎉 ALL TESTS PASSED - SYSTEM READY FOR JARVIS
```

---

## SUPPORT

If deployment fails:
1. Check logs in /tmp/ (scripts write debug info there)
2. Review AUDIT_ANALYSIS.md for expected vs actual
3. Run health check locally: `python3 scripts/system_health_check.py`
4. Check .env credentials are correct

---

**Ready? Execute this command on your server:**

```bash
cd ~/Mark-LIII && bash scripts/server_deployment.sh
```

**Time to deploy: ~15 minutes**  
**Expected result: ✨ JARVIS-READY SYSTEM**
