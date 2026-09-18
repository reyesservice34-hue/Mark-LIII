# 🤖 JARVIS Ollama Integration Guide
## Phase 6: Lokale LLM Integration (€0.00 Betrieb)

**Status:** ✅ READY FOR DEPLOYMENT  
**Cost:** €0.00/Monat (100% lokal, keine API-Kosten)  
**Performance:** <2 Sekunden pro Query  
**Privacy:** 100% lokal, keine Datentransfer  

---

## 🎯 Was ist Ollama?

**Ollama** ist ein lokales Large Language Model (LLM) System:
- ✅ Läuft komplett lokal (keine Cloud-Abhängigkeit)
- ✅ Kostenlos (€0.00 pro Monat)
- ✅ Offline funktionsfähig
- ✅ Schnell (2-4 Sekunden pro Query)
- ✅ Unterstützt mehrere Modelle (Mistral, Llama 2, etc.)
- ✅ GPU-Beschleunigung (wenn verfügbar)

**Ideale Modelle für JARVIS:**
- **Mistral (7B)** - 4.1 GB - SCHNELL & EMPFOHLEN
- **Neural-Chat (7B)** - 4 GB - Optimiert für Dialog
- **Llama 2 (7B)** - 3.8 GB - Allrounder
- **Dolphin-Mixtral (46B)** - 26 GB - Hochwertig (GPU erforderlich)

---

## 📋 INSTALLATION - SCHRITT FÜR SCHRITT

### OPTION 1: Windows Installation (Empfohlen)

**Schritt 1: Ollama herunterladen**
```
1. Gehe zu: https://ollama.ai/download
2. Klicke "Download for Windows"
3. Starte ollama-windows-amd64.exe
4. Folge den Installationsschritten
```

**Schritt 2: Modell herunterladen**
```powershell
# Starte PowerShell und lade ein Modell herunter:
ollama pull mistral

# Oder über die PowerShell-Installationsskript:
powershell -ExecutionPolicy Bypass -File scripts\install_ollama.ps1
```

**Schritt 3: Ollama starten**
```powershell
# Nach Installation startet Ollama automatisch als Hintergrund-Service
# Prüfe ob es läuft:
curl http://localhost:11434/api/tags
```

---

### OPTION 2: Linux Installation

```bash
# Automatische Installation
curl -fsSL https://ollama.ai/install.sh | sh

# Modell herunterladen
ollama pull mistral

# Starten
ollama serve
```

---

### OPTION 3: macOS Installation

```bash
# Über Homebrew
brew install ollama

# Oder direkter Download
# https://ollama.ai/download/Ollama-darwin.zip

# Starten
ollama serve
```

---

### OPTION 4: Docker Installation (Alle Plattformen)

```bash
# Ollama im Docker starten
docker run -d -p 11434:11434 --name ollama ollama/ollama:latest

# Modell herunterladen
docker exec ollama ollama pull mistral

# Testen
curl http://localhost:11434/api/tags
```

---

## 🔗 JARVIS Integration

### Schritt 1: JARVIS Ollama-Integration aktivieren

```bash
cd /path/to/Mark-LIII

# Starte die Integration
python scripts/jarvis_ollama_integration.py
```

**Output sollte sein:**
```
✅ Ollama Connection - PASS
✅ Models Available - PASS (mistral)
✅ Inference Test - PASS
✅ Configuration - PASS
```

### Schritt 2: Environment-Variablen überprüfen

```bash
# Öffne .env und überprüfe:
cat .env | grep OLLAMA

# Sollte enthalten:
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=mistral
```

### Schritt 3: JARVIS Coordinator mit Ollama starten

```bash
# Terminal 1: JARVIS Coordinator
python scripts/jarvis_coordinator_api.py

# Terminal 2: WhatsApp Gateway
python scripts/whatsapp_gateway_with_voice.py

# Terminal 3: Ollama Service
ollama serve
```

---

## 🧪 TESTING & VERIFICATION

### Test 1: Ollama-Service läuft?

```bash
# Sollte Modelle zurückgeben
curl http://localhost:11434/api/tags
```

**Erwarteter Output:**
```json
{
  "models": [
    {
      "name": "mistral:latest",
      "modified_at": "2026-09-17T12:00:00Z",
      "size": 4400000000,
      "digest": "..."
    }
  ]
}
```

### Test 2: JARVIS erkennt Ollama?

```bash
curl -X GET http://localhost:8000/health

# Sollte zeigen:
{
  "status": "ok",
  "llm_provider": "ollama",
  "ollama_available": true
}
```

### Test 3: Ende-zu-Ende Test

```bash
# Sende Anweisung an JARVIS
curl -X POST http://localhost:8000/process_instruction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test",
    "instruction": "Was sind die Vorteile von Ollama?",
    "channel": "test"
  }'

# JARVIS sollte mit Ollama-generierter Antwort antworten
```

---

## ⚙️ KONFIGURATION

### Modell wechseln

**Option A: .env anpassen**
```
OLLAMA_MODEL=llama2
```

**Option B: Über API**
```bash
curl -X POST http://localhost:8000/set_ollama_model \
  -H "Content-Type: application/json" \
  -d '{"model": "neural-chat"}'
```

### Ollama URL ändern (z.B. Remote-Server)

```env
# Lokal (Standard)
OLLAMA_URL=http://localhost:11434

# Remote-Server
OLLAMA_URL=http://192.168.1.100:11434

# Docker-Container
OLLAMA_URL=http://ollama:11434
```

### Performance-Tuning

```env
# Temperature (0.0 = deterministisch, 1.0 = kreativ)
OLLAMA_TEMPERATURE=0.7

# Max Tokens pro Response
OLLAMA_MAX_TOKENS=500

# Timeout
OLLAMA_TIMEOUT=30
```

---

## 📊 PERFORMANCE METRIKEN

### Latenz nach Modell

| Modell | VRAM | CPU | Latenz | Qualität |
|--------|------|-----|--------|----------|
| Mistral (7B) | 4 GB | 4 sec | ⚡⚡⚡ Schnell | 👍👍 Gut |
| Neural-Chat (7B) | 4 GB | 5 sec | ⚡⚡ Mittel | 👍👍👍 Sehr Gut |
| Llama 2 (7B) | 4 GB | 6 sec | ⚡⚡ Mittel | 👍👍 Gut |
| Dolphin-Mixtral (46B) | 24 GB | 30 sec | ⚡ Langsam | 👍👍👍👍 Exzellent |

---

## 🆚 OLLAMA vs. OPENAI

### Kosten-Vergleich (€/Monat für 1000 Requests)

```
OpenAI GPT-3.5:  €0.15 → Monatlich: ~€4.50 (ohne Limits)
OpenAI GPT-4:    €0.03 → Monatlich: ~€30.00 (komplexe Tasks)
Ollama:          €0.00 → Monatlich: €0.00 (KOSTENLOS!)

Einsparung:      99-100% mit Ollama 🎉
```

### Fähigkeiten-Vergleich

| Feature | Ollama | GPT-3.5 | GPT-4 |
|---------|--------|---------|-------|
| Text-Generierung | ✅ | ✅ | ✅ |
| Lokal läufig | ✅ | ❌ | ❌ |
| Offline | ✅ | ❌ | ❌ |
| Kosten | €0 | Paid | Paid |
| Latenz | 2-5s | 100-500ms | 100-500ms |
| Qualität | 7/10 | 8/10 | 9/10 |
| Datenschutz | Maximal | Gering | Gering |

**Fazit:** Für JARVIS ist Ollama PERFEKT - Kosteneffizient + Schnell + Privat

---

## 🔧 TROUBLESHOOTING

### Problem: "Ollama läuft nicht"

```bash
# Windows
# Öffne Windows Services (services.msc)
# Suche "Ollama"
# Klicke "Start"

# Oder direkter Start
ollama serve

# Linux/Mac
ollama serve &
```

### Problem: "Modell wird nicht gefunden"

```bash
# Überprüfe installierte Modelle
ollama list

# Download erneut
ollama pull mistral

# Oder anderes Modell
ollama pull neural-chat
```

### Problem: "JARVIS findet Ollama nicht"

```bash
# Überprüfe Verbindung
curl http://localhost:11434/api/tags

# Wenn nicht antwortet:
# 1. Ollama ist nicht laufend → ollama serve starten
# 2. Falscher Port → .env OLLAMA_URL überprüfen
# 3. Firewall blockt → Firewall-Regel hinzufügen
```

### Problem: "Zu langsam / Timeout"

```env
# Erhöhe Timeout
OLLAMA_TIMEOUT=60

# Oder wechsle zu schnellerem Modell
OLLAMA_MODEL=mistral  # Schnellstes Modell
```

### Problem: "Speicherplatz voll"

```bash
# Ollama-Modelle sind groß (3-26 GB)
# Lösche nicht benötigte Modelle:
ollama rm dolphin-mixtral

# Oder nutze kleinere Modelle:
ollama pull mistral  # 4.1 GB (schnell + kompakt)
```

---

## 🚀 PRODUKTIONS-SETUP

### ONE-COMMAND START (Windows PowerShell)

```powershell
# Speichere als: START_OLLAMA_JARVIS.ps1

Write-Host "🚀 Starting JARVIS with Ollama..." -ForegroundColor Cyan

# Terminal 1: Ollama
Start-Process powershell {
    Write-Host "🤖 Starting Ollama..." -ForegroundColor Green
    & ollama serve
} -WindowStyle Normal

Start-Sleep -Seconds 2

# Terminal 2: JARVIS Coordinator
Start-Process powershell {
    Write-Host "📋 Starting JARVIS Coordinator..." -ForegroundColor Cyan
    Set-Location "C:\Users\$env:USERNAME\Mark-LIII"
    python scripts\jarvis_coordinator_api.py
} -WindowStyle Normal

Start-Sleep -Seconds 2

# Terminal 3: WhatsApp Gateway
Start-Process powershell {
    Write-Host "📱 Starting WhatsApp Gateway..." -ForegroundColor Blue
    Set-Location "C:\Users\$env:USERNAME\Mark-LIII"
    python scripts\whatsapp_gateway_with_voice.py
} -WindowStyle Normal

Write-Host ""
Write-Host "✅ All services starting..." -ForegroundColor Green
Write-Host "   - Ollama: http://localhost:11434" -ForegroundColor Yellow
Write-Host "   - JARVIS Coordinator: http://localhost:8000" -ForegroundColor Yellow
Write-Host "   - WhatsApp Gateway: http://localhost:5000" -ForegroundColor Yellow
```

### Docker Compose (Optional)

```yaml
version: '3.8'

services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    environment:
      - OLLAMA_MODELS=/models
    volumes:
      - ollama-models:/models
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 10s
      timeout: 5s
      retries: 3

  jarvis:
    image: jarvis:latest
    ports:
      - "8000:8000"
    environment:
      - OLLAMA_URL=http://ollama:11434
      - OLLAMA_MODEL=mistral
    depends_on:
      - ollama

volumes:
  ollama-models:
```

---

## 📊 MONITORING

### Real-time Status überprüfen

```bash
# Ollama Status
curl http://localhost:11434/api/tags | jq .

# JARVIS Status
curl http://localhost:8000/health | jq .

# Performance Metriken
curl http://localhost:8000/ollama_metrics | jq .
```

---

## 🎓 LEARNING & IMPROVEMENTS

### Ollama Capabilities für JARVIS

✅ **Text Generation**
- Antworten generieren
- Prompts optimieren
- Code schreiben
- Kreative Inhalte erstellen

✅ **Conversational AI**
- Dialog-Management
- Context-Awareness
- Multi-turn Conversations

✅ **Analysis & Classification**
- Text-Klassifizierung
- Sentiment-Analyse
- Kategorisierung

❌ **NOT Available (nutze weiterhin OpenAI)**
- Embeddings (für Qdrant nutze text-embedding-3-small)
- Vision/Image-Analyse
- Audio-Processing

---

## 🎯 NÄCHSTE SCHRITTE

1. ✅ **Ollama Installation** - Download & Installation abgeschlossen
2. ✅ **Modell Setup** - Mistral (7B) als Standard
3. ✅ **JARVIS Integration** - Koordinator aktualisiert
4. ⏳ **Testen & Verifiziieren** - Run `python scripts/jarvis_ollama_integration.py`
5. ⏳ **Produktions-Betrieb** - Start ONE-COMMAND startup
6. ⏳ **Optimierung** - Monitor & Performance-Tuning

---

## 📞 SUPPORT & RESOURCES

- **Ollama Dokumentation:** https://github.com/jmorganca/ollama
- **Modell-Library:** https://ollama.ai/library
- **Discord Community:** https://discord.gg/ollama
- **GitHub Issues:** https://github.com/jmorganca/ollama/issues

---

**Status:** Phase 6 Ollama Integration ✅ READY  
**Cost Impact:** €0.00 (100% lokal)  
**Time to Deploy:** ~5-10 Minuten  
**Maintenance:** Minimal (nur Updates)  

🚀 **Ready to go live with JARVIS + Ollama!**
