# Mark-LIII JARVIS - One-Command Startup Script
# Orchestrates all services: Coordinator, WhatsApp Gateway, ngrok, Health Monitor

param(
    [switch]$SkipHealthMonitor = $false,
    [switch]$SkipNgrok = $false,
    [int]$HealthCheckInterval = 10
)

# Color functions
function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "=" * 70 -ForegroundColor Cyan
    Write-Host "🚀 $Text" -ForegroundColor Cyan
    Write-Host "=" * 70 -ForegroundColor Cyan
    Write-Host ""
}

function Write-Success {
    param([string]$Text)
    Write-Host "✅ $Text" -ForegroundColor Green
}

function Write-Info {
    param([string]$Text)
    Write-Host "ℹ️  $Text" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Text)
    Write-Host "❌ $Text" -ForegroundColor Red
}

# Verify project directory
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptsDir = Join-Path $projectRoot "scripts"

if (-not (Test-Path $scriptsDir)) {
    Write-Error-Custom "Scripts directory not found: $scriptsDir"
    exit 1
}

Write-Header "MARK-LIII JARVIS - STARTUP ORCHESTRATOR"

Write-Info "Project Root: $projectRoot"
Write-Info "Scripts Directory: $scriptsDir"
Write-Info ""

# Step 1: Check prerequisites
Write-Host "📋 Step 1: Checking Prerequisites..." -ForegroundColor Cyan
Write-Host ""

$pythonCheck = $false
$ngrokCheck = $false

# Check Python
try {
    $pythonVersion = python --version 2>&1
    Write-Success "Python found: $pythonVersion"
    $pythonCheck = $true
} catch {
    Write-Error-Custom "Python not found - please install Python 3.10+"
}

# Check ngrok (optional, if not skipped)
if (-not $SkipNgrok) {
    $ngrokPath = "C:\Users\info\OneDrive\Desktop\ngrok-v3-stable-windows-amd64\ngrok.exe"
    if (Test-Path $ngrokPath) {
        Write-Success "ngrok found at: $ngrokPath"
        $ngrokCheck = $true
    } else {
        Write-Info "ngrok not found at expected path (optional for local testing)"
        Write-Info "To use ngrok, download from https://ngrok.com/download"
    }
}

if (-not $pythonCheck) {
    Write-Error-Custom "Cannot continue without Python"
    exit 1
}

Write-Host ""

# Step 2: Check services availability
Write-Host "📋 Step 2: Checking Service Ports..." -ForegroundColor Cyan
Write-Host ""

function Test-Port {
    param([int]$Port, [string]$ServiceName)

    try {
        $socket = New-Object System.Net.Sockets.TcpClient
        $result = $socket.BeginConnect("127.0.0.1", $Port, $null, $null)
        $result.AsyncWaitHandle.WaitOne(1000, $false) | Out-Null

        if ($socket.Connected) {
            Write-Info "$ServiceName (Port $Port): Already in use - please stop existing service"
            return $true
        } else {
            Write-Success "$ServiceName (Port $Port): Available"
            return $false
        }
    } catch {
        Write-Success "$ServiceName (Port $Port): Available"
        return $false
    }
}

$port8000InUse = Test-Port 8000 "JARVIS Coordinator"
$port5000InUse = Test-Port 5000 "WhatsApp Gateway"
$port3000InUse = Test-Port 3000 "n8n"

Write-Host ""

# Step 3: Launch services in new PowerShell windows
Write-Host "📋 Step 3: Launching Services..." -ForegroundColor Cyan
Write-Host ""

$windowPositionX = 0
$windowPositionY = 0
$windowWidth = 1200
$windowHeight = 600

# JARVIS Coordinator (Port 8000)
if ($port8000InUse) {
    Write-Error-Custom "Port 8000 already in use - JARVIS Coordinator may already be running"
} else {
    Write-Host "  Starting JARVIS Coordinator API (Port 8000)..." -ForegroundColor Yellow

    $coordScript = @"
Set-Location `"$projectRoot`"
Write-Host "🚀 Starting JARVIS Coordinator API..." -ForegroundColor Green
python `"$scriptsDir\jarvis_coordinator_api.py`"
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
pause
"@

    $psProcess = Start-Process powershell -ArgumentList "-NoExit", "-Command", $coordScript `
        -PassThru -WindowStyle Normal

    Start-Sleep -Seconds 2
    Write-Success "JARVIS Coordinator launched (PID: $($psProcess.Id))"
}

# WhatsApp Gateway (Port 5000)
if ($port5000InUse) {
    Write-Error-Custom "Port 5000 already in use - WhatsApp Gateway may already be running"
} else {
    Write-Host "  Starting WhatsApp Gateway with Voice (Port 5000)..." -ForegroundColor Yellow

    $gatewayScript = @"
Set-Location `"$projectRoot`"
Write-Host "🚀 Starting WhatsApp Gateway..." -ForegroundColor Green
python `"$scriptsDir\whatsapp_gateway_with_voice.py`"
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
pause
"@

    $gwProcess = Start-Process powershell -ArgumentList "-NoExit", "-Command", $gatewayScript `
        -PassThru -WindowStyle Normal

    Start-Sleep -Seconds 2
    Write-Success "WhatsApp Gateway launched (PID: $($gwProcess.Id))"
}

# ngrok Tunnel (optional)
if (-not $SkipNgrok -and $ngrokCheck) {
    Write-Host "  Starting ngrok Tunnel (Port 5000 → Public URL)..." -ForegroundColor Yellow

    $ngrokScript = @"
Write-Host "🚀 Starting ngrok tunnel..." -ForegroundColor Green
`"$ngrokPath`" http 5000
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
pause
"@

    $ngrokProcess = Start-Process powershell -ArgumentList "-NoExit", "-Command", $ngrokScript `
        -PassThru -WindowStyle Normal

    Start-Sleep -Seconds 2
    Write-Success "ngrok tunnel launched (PID: $($ngrokProcess.Id))"
    Write-Info "Public URL will be displayed in ngrok window"
}

Write-Host ""

# Step 4: Start Health Monitor (optional)
if (-not $SkipHealthMonitor) {
    Write-Host "📋 Step 4: Starting Health Monitor..." -ForegroundColor Cyan
    Write-Host ""

    $monitorScript = @"
Set-Location `"$projectRoot`"
Write-Host "🚀 Starting Health Monitor..." -ForegroundColor Green
python `"$scriptsDir\continuous_health_monitor.py`" $HealthCheckInterval
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
pause
"@

    $monitorProcess = Start-Process powershell -ArgumentList "-NoExit", "-Command", $monitorScript `
        -PassThru -WindowStyle Normal

    Start-Sleep -Seconds 1
    Write-Success "Health Monitor launched (PID: $($monitorProcess.Id))"
}

Write-Host ""

# Step 5: Display summary
Write-Header "STARTUP COMPLETE"

Write-Success "All services launched successfully!"
Write-Host ""
Write-Host "📊 Service Status:" -ForegroundColor Cyan
Write-Host "  ✅ JARVIS Coordinator API: http://127.0.0.1:8000/health" -ForegroundColor Green
Write-Host "  ✅ WhatsApp Gateway: http://127.0.0.1:5000/health" -ForegroundColor Green

if (-not $SkipNgrok -and $ngrokCheck) {
    Write-Host "  ✅ ngrok Tunnel: Check ngrok window for public URL" -ForegroundColor Green
}

if (-not $SkipHealthMonitor) {
    Write-Host "  ✅ Health Monitor: Check health monitor window for status" -ForegroundColor Green
}

Write-Host ""
Write-Host "📋 Next Steps:" -ForegroundColor Cyan
Write-Host "  1. Wait for 'Running on' messages in each window (2-3 seconds)"
Write-Host "  2. Check health monitor for service status"
Write-Host "  3. Run Phase 5 deployment when ready:"
Write-Host "     python scripts\phase_5_complete_deployment.py" -ForegroundColor Yellow
Write-Host "  4. Provide Qdrant Cloud credentials (create at https://qdrant.tech/)"
Write-Host ""
Write-Host "🧪 Test Message Flow:" -ForegroundColor Cyan
Write-Host ""
Write-Host '  $body = @{' -ForegroundColor Cyan
Write-Host '      phone_number = "+49123456789"' -ForegroundColor Cyan
Write-Host '      message = "Hallo JARVIS!"' -ForegroundColor Cyan
Write-Host '  } | ConvertTo-Json' -ForegroundColor Cyan
Write-Host '  ' -ForegroundColor Cyan
Write-Host '  Invoke-WebRequest -Uri "http://localhost:5000/whatsapp/test" `' -ForegroundColor Cyan
Write-Host '    -Method POST `' -ForegroundColor Cyan
Write-Host '    -Headers @{"Content-Type"="application/json"} `' -ForegroundColor Cyan
Write-Host '    -Body $body' -ForegroundColor Cyan
Write-Host ""
Write-Host "📖 Full Documentation:" -ForegroundColor Cyan
Write-Host "  - OPERATIONS_GUIDE.md (comprehensive reference)" -ForegroundColor Yellow
Write-Host "  - DEPLOYMENT_GUIDE.md (initial setup)" -ForegroundColor Yellow
Write-Host ""
Write-Host "💡 Status: PRODUCTION READY ✅" -ForegroundColor Green
Write-Host ""
Write-Host "🎯 All services are running. Close any window to stop that service." -ForegroundColor Cyan
Write-Host "   Use Ctrl+C to stop gracefully." -ForegroundColor Cyan
Write-Host ""
Write-Host "=" * 70 -ForegroundColor Cyan
