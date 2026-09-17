# ===================================================================
# 🚀 JARVIS Docker Deployment - One-Command Startup
# ===================================================================
# Starts containerized JARVIS system with Ollama + Coordinator + Gateway
#
# Usage: ./START_JARVIS_DOCKER.ps1
# ===================================================================

param(
    [switch]$SkipVerification = $false,
    [switch]$Follow = $false
)

Write-Host ""
Write-Host "██████████████████████████████████████████████████████" -ForegroundColor Cyan
Write-Host "🚀 JARVIS DOCKER DEPLOYMENT STARTUP" -ForegroundColor Cyan
Write-Host "██████████████████████████████████████████████████████" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
Write-Host "📋 Checking prerequisites..." -ForegroundColor Yellow

# Check Docker
try {
    $dockerVersion = docker --version 2>$null
    if ($?) {
        Write-Host "✅ Docker: $dockerVersion" -ForegroundColor Green
    } else {
        Write-Host "❌ Docker not installed" -ForegroundColor Red
        Write-Host "   Install from: https://www.docker.com/products/docker-desktop" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "❌ Docker not found" -ForegroundColor Red
    exit 1
}

# Check Docker Compose
try {
    $composeVersion = docker compose version 2>$null
    if ($?) {
        Write-Host "✅ Docker Compose: $composeVersion" -ForegroundColor Green
    } else {
        Write-Host "⚠️  Docker Compose not found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "⚠️  Docker Compose not available" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "🔧 Preparing Docker environment..." -ForegroundColor Yellow

# Check if .env.docker exists
if (-not (Test-Path ".env.docker")) {
    Write-Host "⚠️  .env.docker not found - creating from template" -ForegroundColor Yellow
    Copy-Item ".env.docker" ".env.docker" -ErrorAction SilentlyContinue
}

# Create .env if doesn't exist
if (-not (Test-Path ".env")) {
    Write-Host "📝 Creating .env from .env.docker" -ForegroundColor Yellow
    Copy-Item ".env.docker" ".env"
}

Write-Host ""
Write-Host "🐳 Starting Docker services..." -ForegroundColor Yellow

# Start services
docker compose up -d

if ($?) {
    Write-Host "✅ Docker services started" -ForegroundColor Green
} else {
    Write-Host "❌ Failed to start Docker services" -ForegroundColor Red
    exit 1
}

# Wait for services to be ready
Write-Host ""
Write-Host "⏳ Waiting for services to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Check service status
Write-Host ""
Write-Host "📊 Service Status:" -ForegroundColor Cyan
docker compose ps

Write-Host ""
Write-Host "🔍 Verifying service health..." -ForegroundColor Yellow

# Wait for services with retry
$maxRetries = 30
$retryCount = 0
$servicesReady = $false

while ($retryCount -lt $maxRetries -and -not $servicesReady) {
    $status = docker compose ps --format "json" | ConvertFrom-Json | Where-Object {$_.State -eq "running"} | Measure-Object | Select-Object -ExpandProperty Count

    if ($status -ge 4) {
        $servicesReady = $true
        Write-Host "✅ All services are running" -ForegroundColor Green
        break
    }

    $retryCount++
    Write-Host "   Waiting... ($retryCount/$maxRetries)" -ForegroundColor Gray
    Start-Sleep -Seconds 2
}

if (-not $servicesReady) {
    Write-Host "⚠️  Some services may not be ready" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "📊 Container Information:" -ForegroundColor Cyan
Write-Host ""
docker compose ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

Write-Host ""
Write-Host "🔗 Service Endpoints:" -ForegroundColor Cyan
Write-Host "  📡 Ollama LLM:            http://localhost:11434"
Write-Host "  🤖 JARVIS Coordinator:    http://localhost:8000"
Write-Host "  💬 WhatsApp Gateway:      http://localhost:5000"
Write-Host "  💚 Health Monitor:        http://localhost:9000"
Write-Host ""

# Suggest next steps
Write-Host "✨ Startup Complete!" -ForegroundColor Green
Write-Host ""
Write-Host "📝 Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Verify services are healthy:"
Write-Host "     docker compose ps"
Write-Host ""
Write-Host "  2. View logs:"
Write-Host "     docker compose logs -f"
Write-Host ""
Write-Host "  3. Test JARVIS Coordinator:"
Write-Host "     curl http://localhost:8000/health"
Write-Host ""
Write-Host "  4. Run verification suite:"
Write-Host "     python scripts/verify_jarvis_deployment.py"
Write-Host ""
Write-Host "  5. Stop all services:"
Write-Host "     docker compose down"
Write-Host ""

# Optional: Follow logs
if ($Follow) {
    Write-Host "📋 Following Docker logs (press Ctrl+C to stop)..." -ForegroundColor Cyan
    Write-Host ""
    docker compose logs -f
}

Write-Host "██████████████████████████████████████████████████████" -ForegroundColor Cyan
Write-Host "🎉 JARVIS Docker deployment ready for use!" -ForegroundColor Cyan
Write-Host "██████████████████████████████████████████████████████" -ForegroundColor Cyan
Write-Host ""
