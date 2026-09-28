# Ollama Installation für Windows
# Kostenlose lokale KI - 100% lokal, keine API Kosten

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "OLLAMA INSTALLATION - Kostenlose Lokale KI" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# Check if Ollama already installed
$ollamaPath = "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe"
$ollamaExists = Test-Path $ollamaPath

if ($ollamaExists) {
    Write-Host "Ollama ist bereits installiert!" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "Installiere Ollama..." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Gehe zu: https://ollama.ai/download" -ForegroundColor Cyan
    Write-Host "2. Download 'Ollama for Windows'" -ForegroundColor Cyan
    Write-Host "3. Installiere und starte" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Oder via PowerShell (Administrator):" -ForegroundColor Cyan
    Write-Host "  winget install Ollama.Ollama" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Warte bis Installation komplett..." -ForegroundColor Yellow
    Read-Host "Drück Enter wenn installiert"
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "OLLAMA MODELL HERUNTERLADEN" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Verfügbare Modelle:" -ForegroundColor Cyan
Write-Host "  1. mistral (schnell, 4.1GB) - EMPFOHLEN" -ForegroundColor Yellow
Write-Host "  2. neural-chat (schnell, 4GB)" -ForegroundColor Yellow
Write-Host "  3. dolphin-mixtral (besser, 26GB)" -ForegroundColor Yellow
Write-Host "  4. llama2 (beliebig, 3.8GB)" -ForegroundColor Yellow
Write-Host ""

$model = Read-Host "Welches Modell? (1=mistral, 2=neural-chat, 3=dolphin-mixtral, 4=llama2, default=1)"

switch ($model) {
    "2" { $modelName = "neural-chat" }
    "3" { $modelName = "dolphin-mixtral" }
    "4" { $modelName = "llama2" }
    default { $modelName = "mistral" }
}

Write-Host ""
Write-Host "Starten: ollama pull $modelName" -ForegroundColor Cyan
Write-Host "Größe wird heruntergeladen (kann 5-10 Minuten dauern)..." -ForegroundColor Yellow
Write-Host ""

# Start Ollama service (if not running)
$ollamaProcess = Get-Process ollama -ErrorAction SilentlyContinue
if (-not $ollamaProcess) {
    Write-Host "Starte Ollama Service..." -ForegroundColor Yellow
    Start-Process $ollamaPath -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# Pull model
& ollama pull $modelName

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host "OLLAMA BEREIT!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Modell: $modelName" -ForegroundColor Green
Write-Host "Status: READY" -ForegroundColor Green
Write-Host "API: http://localhost:11434" -ForegroundColor Green
Write-Host ""
Write-Host "Nächster Schritt: Integriere Ollama in JARVIS" -ForegroundColor Cyan
Write-Host "  python scripts\integrate_ollama.py" -ForegroundColor Yellow
Write-Host ""
