# 🐳 JARVIS Docker Deployment Guide
## One-Command Production Setup

**Status:** ✅ Production Ready  
**Cost:** €0.00 (local + free tiers)  
**Uptime:** 24/7 (auto-restart on failure)  
**Performance:** Optimized with container networking  

---

## 📋 Prerequisites

### Windows Setup
1. **Docker Desktop for Windows**
   - Download: https://www.docker.com/products/docker-desktop
   - Install and restart your machine
   - Verify: `docker --version`

2. **Git** (already installed)
   - Verify: `git --version`

3. **Windows 10/11 with WSL2** (Docker will ask you to enable this)

### macOS/Linux Setup
1. **Docker & Docker Compose**
   ```bash
   # macOS (via Homebrew)
   brew install docker docker-compose
   
   # Linux (via apt)
   sudo apt-get install docker.io docker-compose
   ```

---

## 🚀 Quick Start (30 seconds)

```powershell
# 1. Navigate to Mark-LIII
cd C:\Users\info\Mark-LIII

# 2. Start all services
docker-compose up -d

# 3. Check status
docker-compose ps

# That's it! All services running.
```

---

## 📊 What Runs in Docker

| Service | Port | Status | Auto-Restart |
|---------|------|--------|--------------|
| **Ollama LLM** | 11434 | ✅ | Yes |
| **JARVIS Coordinator** | 8000 | ✅ | Yes |
| **WhatsApp Gateway** | 5000 | ✅ | Yes |
| **Health Monitor** | 9000 | ✅ | Yes |

---

## 🎮 Common Commands

### Start Services
```powershell
docker-compose up -d
```

### Stop Services
```powershell
docker-compose down
```

### View Logs
```powershell
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f jarvis-coordinator

# Last 100 lines
docker-compose logs --tail 100
```

### Check Status
```powershell
docker-compose ps
```

### Restart Service
```powershell
docker-compose restart jarvis-coordinator
```

### Remove Everything (fresh start)
```powershell
docker-compose down -v
```

---

## 🧪 Testing

### Test All Services
```powershell
# Test Ollama
curl http://localhost:11434/api/tags

# Test JARVIS Coordinator
curl http://localhost:8000/health

# Test WhatsApp Gateway
curl http://localhost:5000/health
```

### Test End-to-End (JARVIS + Ollama)
```powershell
$body = @{
    user_id = "master"
    instruction = "Was kannst du mit Ollama machen?"
    channel = "test"
} | ConvertTo-Json

Invoke-RestMethod -Uri http://localhost:8000/process_instruction `
    -Method POST `
    -Headers @{"Content-Type"="application/json"} `
    -Body $body
```

---

## 📊 Performance Benefits

| Aspect | Without Docker | With Docker |
|--------|---|---|
| **Setup Time** | 15 min (manual) | 30 sec (one command) |
| **Failure Recovery** | Manual restart | Auto-restart |
| **Isolation** | System-wide | Container-isolated |
| **Scaling** | Manual | Single docker-compose |
| **Version Control** | Manual tracking | Built-in |
| **Portability** | System-dependent | Works everywhere |

---

## 🔧 Advanced Configuration

### Use Custom .env
```powershell
# Copy and customize
cp .env.docker .env

# Edit .env with your Twilio/OpenAI credentials (optional)
notepad .env

# Restart services to apply
docker-compose restart
```

### View Service Logs in Real-Time
```powershell
docker-compose logs -f jarvis-coordinator --tail 50
```

### Execute Commands in Running Container
```powershell
# Connect to JARVIS container
docker exec -it jarvis-coordinator bash

# Run Python command
docker exec jarvis-coordinator python -c "print('Hello from Docker')"
```

### Monitor Resource Usage
```powershell
docker stats
```

---

## 🆘 Troubleshooting

### Port Already in Use
```powershell
# Find what's using the port (example: 8000)
netstat -ano | findstr :8000

# Kill the process
taskkill /PID <PID> /F

# Or change port in docker-compose.yml
# "8000:8000" → "8001:8000"
```

### Container Crashes Immediately
```powershell
# Check logs
docker-compose logs jarvis-coordinator

# Rebuild container
docker-compose build --no-cache
docker-compose up -d
```

### Docker Daemon Not Running
```powershell
# Start Docker Desktop manually, or:
wsl --list --verbose  # Check if WSL2 is running
```

### Out of Disk Space
```powershell
# Clean unused Docker resources
docker system prune -a

# Then restart services
docker-compose up -d
```

---

## 📈 Monitoring

### Auto-Monitoring Dashboard
```powershell
# The Health Monitor service runs automatically
# Access via:
curl http://localhost:9000/health
```

### Check Service Health
```powershell
docker-compose ps
# All should show "Up" status
```

### View Service Logs
```powershell
docker-compose logs --follow
```

---

## 🎯 Production Deployment

For 24/7 production use:

1. ✅ Docker containers auto-restart on failure
2. ✅ Health checks ensure service availability
3. ✅ Logs persisted for debugging
4. ✅ Network isolation for security
5. ✅ Volume persistence for data

### Enable Auto-Start on System Boot (Windows)
```powershell
# Create batch file: start-jarvis.bat
@echo off
cd C:\Users\info\Mark-LIII
docker-compose up -d

# Schedule in Windows Task Scheduler:
# - Trigger: At startup
# - Action: Run start-jarvis.bat
# - Run with highest privileges: Yes
```

---

## 💰 Cost Analysis

```
Docker Deployment: €0.00
- Ollama: Free (local)
- JARVIS: Free (local)
- WhatsApp: Free testing (Twilio trial: €15.50 one-time)
- Total: €0.00 ongoing (100% local)

vs Manual Setup:
- Hourly troubleshooting
- Manual restarts
- System dependencies
- Cost of downtime
```

---

## ✅ Verification Checklist

After `docker-compose up -d`:

- [ ] `docker-compose ps` shows 4 services "Up"
- [ ] `curl http://localhost:8000/health` returns OK
- [ ] `curl http://localhost:5000/health` returns OK
- [ ] `curl http://localhost:11434/api/tags` returns models
- [ ] WhatsApp Gateway logs show "Ready"
- [ ] JARVIS responds to test instructions
- [ ] All services auto-restart after failure

---

## 🚀 Next Steps

1. **Start Docker deployment**
   ```powershell
   docker-compose up -d
   ```

2. **Verify all services**
   ```powershell
   docker-compose ps
   ```

3. **Test WhatsApp integration**
   ```powershell
   # Send message to WhatsApp webhook
   # Receive response from JARVIS
   ```

4. **Monitor in real-time**
   ```powershell
   docker-compose logs -f
   ```

---

**Status:** Docker infrastructure ready for 24/7 production deployment ✅

🎉 **One command, infinite reliability!**
