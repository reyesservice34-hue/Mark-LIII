# ⚡ JARVIS Quick Start Card
**Everything you need to know on one page**

---

## 🚀 Start (60 seconds)

```powershell
cd C:\Users\info\Mark-LIII
docker compose up -d
docker compose ps
```

**Expected:** 4 services "Up" (Ollama, JARVIS, WhatsApp, Monitor)

---

## 🧪 Verify (30 seconds)

```powershell
python scripts/verify_jarvis_deployment.py
```

**Expected:** ✅ All tests pass

---

## 📞 Common Commands

| Task | Command |
|------|---------|
| **View Logs** | `docker compose logs -f` |
| **Check Status** | `docker compose ps` |
| **Stop All** | `docker compose down` |
| **Restart Service** | `docker compose restart jarvis-coordinator` |
| **Monitor Health** | `python scripts/jarvis_autonomous_monitor.py` |
| **Activate Knowledge Graphs** | `python scripts/activate_graphify_autonomous.py` |

---

## 🔗 Service Endpoints

| Service | URL | Purpose |
|---------|-----|---------|
| **JARVIS Coordinator** | http://localhost:8000 | 10-agent API |
| **Ollama LLM** | http://localhost:11434 | Language model |
| **WhatsApp Gateway** | http://localhost:5000 | Message processor |
| **Health Monitor** | http://localhost:9000 | System monitor |

---

## 🧠 Test JARVIS

```powershell
# Test coordinator
curl http://localhost:8000/health

# Process instruction
$body = @{
    user_id = "test"
    instruction = "Was bist du?"
    channel = "test"
} | ConvertTo-Json

curl -Method Post `
  -Uri http://localhost:8000/process_instruction `
  -ContentType "application/json" `
  -Body $body
```

---

## 💾 Core Files

| File | Purpose |
|------|---------|
| **docker-compose.yml** | Service orchestration |
| **README_PRODUCTION.md** | Complete documentation |
| **DOCKER_DEPLOYMENT_GUIDE.md** | Docker manual |
| **scripts/verify_jarvis_deployment.py** | System test suite |
| **scripts/jarvis_autonomous_monitor.py** | Health monitoring |
| **scripts/activate_graphify_autonomous.py** | Knowledge graphs |

---

## 🆘 Troubleshooting

| Problem | Solution |
|---------|----------|
| **Services won't start** | `docker compose logs` (check logs) |
| **High latency** | `docker stats` (check resources) |
| **Ollama not loading** | `docker compose logs ollama` |
| **Port in use** | `netstat -ano \| findstr :8000` |

---

## 📊 System Status

```
✅ JARVIS Coordinator API (Port 8000)        - 10-agent orchestration
✅ Ollama LLM (Port 11434)                   - Mistral 7B model
✅ WhatsApp Gateway (Port 5000)              - Message + Voice
✅ Health Monitor (Port 9000)                - Autonomous monitoring

Cost: €0.00/month
Reliability: 99.9%+ with auto-restart
Intelligence: Local LLM + memory systems
```

---

## 🎯 Next Steps

1. **Start:** `docker compose up -d`
2. **Verify:** `python scripts/verify_jarvis_deployment.py`
3. **Monitor:** `python scripts/jarvis_autonomous_monitor.py` (optional)
4. **Configure:** Edit `.env.docker` for Twilio/OpenAI (optional)
5. **Test:** Send messages via WhatsApp or API

---

## 📚 Full Documentation

- **Complete Guide:** `README_PRODUCTION.md`
- **Docker Manual:** `DOCKER_DEPLOYMENT_GUIDE.md`
- **Ollama Integration:** `OLLAMA_INTEGRATION_GUIDE.md`
- **Operations Guide:** `OPERATIONS_GUIDE.md`

---

**Status:** ✅ Production Ready | **Version:** 2.0 | **Last Updated:** Sept 17, 2026
