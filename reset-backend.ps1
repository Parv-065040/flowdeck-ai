# reset-backend.ps1
# Bulletproof backend reset for Flowdeck AI - Day 2.
#
# Fixes every failure mode hit during Day 2 testing in one script:
#  - Wrong working directory (docker compose needs to run next to docker-compose.yml)
#  - Stale containers/env   (docker compose restart does NOT reload .env - must be down+up)
#  - Transient PyPI failures (retries the build a few times before giving up)
#  - OneDrive file-watcher IO errors (detects the OneDrive path and warns, doesn't crash silently)
#  - No easy way to confirm health afterward (this checks /healthz automatically at the end)
#
# USAGE:
#  Right-click this file -> Run with PowerShell
#  OR, from a PowerShell prompt already in the flowdeck-ai folder: .\reset-backend.ps1
#
# This script does NOT touch cloudflared - start that separately in its own terminal
# AFTER this script reports healthy, since it needs a fresh terminal window that stays open.

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
  Write-Host ""
  Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
  Write-Host "  OK: $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
  Write-Host "  WARNING: $msg" -ForegroundColor Yellow
}

function Write-Fail($msg) {
  Write-Host "  FAILED: $msg" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# Step 1 - confirm we're in the right folder (docker-compose.yml must exist here)
# ---------------------------------------------------------------------------
Write-Step "Checking working directory"

if (-not (Test-Path ".\docker-compose.yml")) {
  Write-Fail "No docker-compose.yml found in the current folder: $(Get-Location)"
  Write-Host ""
  Write-Host "This script must be run from inside the flowdeck-ai project folder" -ForegroundColor Yellow
  Write-Host "(the same folder that contains docker-compose.yml, backend/, frontend/, etc.)" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "Fix: cd to that folder first, e.g.:" -ForegroundColor Yellow
  Write-Host ' cd "C:\Users\Parv\OneDrive\Desktop\AI Sales Agent Demo\Production\flowdeck-ai"' -ForegroundColor White
  exit 1
}
Write-Ok "Found docker-compose.yml in $(Get-Location)"

# Warn (don't block) if this is on OneDrive - known cause of a WatchFiles crash during Day 2 testing
if ((Get-Location).Path -match "OneDrive") {
  Write-Warn "This project folder is inside OneDrive."
  Write-Warn "OneDrive's background sync has been observed to crash Docker's file watcher"
  Write-Warn "(WatchfilesRustInternalError / IO error). If backend containers restart on their"
  Write-Warn "own with no code change, this is the most likely cause. Not fixed by this script --"
  Write-Warn "consider moving the project outside OneDrive, or right-click the folder in File"
  Write-Warn "Explorer -> 'Always keep on this device' to stop cloud sync from touching it."
}

# ---------------------------------------------------------------------------
# Step 2 - confirm .env exists and has the required Agora keys (fail fast, not deep in a build log)
# ---------------------------------------------------------------------------
Write-Step "Checking .env for required keys"

if (-not (Test-Path ".\.env")) {
  Write-Fail "No .env file found. Copy .env.example to .env and fill in real values first."
  exit 1
}

$envContent = Get-Content ".\.env" -Raw
$requiredKeys = @("AGORA_APP_ID", "AGORA_APP_CERTIFICATE")
$missing = @()
foreach ($key in $requiredKeys) {
  if ($envContent -notmatch "(?m)^\s*$key\s*=\s*\S+") {
    $missing += $key
  }
}
if ($missing.Count -gt 0) {
  Write-Fail "Missing or empty in .env: $($missing -join ', ')"
  Write-Host "Run 'agora project env --shell --with-secrets' from this folder to get real values." -ForegroundColor Yellow
  exit 1
}
Write-Ok "AGORA_APP_ID and AGORA_APP_CERTIFICATE are present in .env"

# ---------------------------------------------------------------------------
# Step 3 - full clean restart (down + up, NOT restart, so .env changes actually apply)
# ---------------------------------------------------------------------------
Write-Step "Stopping any existing containers (docker compose down)"
docker compose down
if ($LASTEXITCODE -ne 0) {
  Write-Warn "docker compose down reported a non-zero exit code (often fine if nothing was running)"
}

Write-Step "Building and starting containers (docker compose up --build -d)"
Write-Host "  (this can take 20-90s; PyPI downloads occasionally fail transiently and are retried below)"

$maxAttempts = 3
$built = $false
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
  Write-Host ""
  Write-Host "  Attempt $attempt of $maxAttempts..." -ForegroundColor DarkGray
  docker compose up --build -d
  if ($LASTEXITCODE -eq 0) {
    $built = $true
    break
  }
  Write-Warn "Build/start failed on attempt $attempt."
  if ($attempt -lt $maxAttempts) {
    Write-Host "  Retrying in 5 seconds (this usually fixes transient PyPI/registry blips)..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 5
  }
}

if (-not $built) {
  Write-Fail "docker compose up --build -d failed $maxAttempts times in a row."
  Write-Host "This is no longer a transient issue -- check:" -ForegroundColor Yellow
  Write-Host " - Internet connection (DNS resolution to registry-1.docker.io / pypi.org)" -ForegroundColor Yellow
  Write-Host " - Docker Desktop is actually running" -ForegroundColor Yellow
  Write-Host " - Antivirus/VPN interfering with HTTPS downloads" -ForegroundColor Yellow
  exit 1
}
Write-Ok "Containers built and started"

# ---------------------------------------------------------------------------
# Step 4 - wait for the backend to actually respond, then check /healthz
# ---------------------------------------------------------------------------
Write-Step "Waiting for backend to become healthy"

$maxWaitSeconds = 60
$elapsed = 0
$healthy = $false
$lastResponse = $null

while ($elapsed -lt $maxWaitSeconds) {
  try {
    $lastResponse = Invoke-RestMethod -Uri "http://localhost:8000/healthz" -TimeoutSec 3 -UseBasicParsing
    $healthy = $true
    break
  } catch {
    Start-Sleep -Seconds 2
    $elapsed += 2
  }
}

if (-not $healthy) {
  Write-Fail "Backend did not respond on http://localhost:8000/healthz within $maxWaitSeconds seconds."
  Write-Host "Check the logs for the real error:" -ForegroundColor Yellow
  Write-Host " docker compose logs backend --tail 50" -ForegroundColor White
  exit 1
}

Write-Ok "Backend responded"
Write-Host ""
Write-Host "  Health check results:" -ForegroundColor White
$lastResponse.checks.PSObject.Properties | ForEach-Object {
  $status = $_.Value
  if ($status -eq "ok") {
    Write-Host "   $($_.Name): $status" -ForegroundColor Green
  } elseif ($status -match "disabled") {
    Write-Host "   $($_.Name): $status" -ForegroundColor DarkGray
  } else {
    Write-Host "   $($_.Name): $status" -ForegroundColor Red
  }
}

$anyBad = $lastResponse.checks.PSObject.Properties | Where-Object {
  $_.Value -ne "ok" -and $_.Value -notmatch "disabled"
}
if ($anyBad) {
  Write-Warn "One or more dependencies are not healthy - check the values above."
} else {
  Write-Ok "All active dependencies are healthy"
}

# ---------------------------------------------------------------------------
# Step 5 - clear next steps (cloudflared is deliberately NOT started here)
# ---------------------------------------------------------------------------
Write-Step "Backend is up. Next steps:"
Write-Host ""
Write-Host " 1. In a NEW terminal window (leave this one), start the tunnel:" -ForegroundColor White
Write-Host "    cloudflared tunnel --url http://localhost:8000" -ForegroundColor Cyan
Write-Host "   Copy the https://....trycloudflare.com URL it prints." -ForegroundColor White
Write-Host ""
Write-Host " 2. In ANOTHER new terminal, watch live logs while testing:" -ForegroundColor White
Write-Host "    cd `"$(Get-Location)`"" -ForegroundColor Cyan
Write-Host "    docker compose logs -f backend" -ForegroundColor Cyan
Write-Host ""
Write-Host " 3. Open demo/agora_test_client.html, paste the tunnel URL, pick a FRESH" -ForegroundColor White
Write-Host "   channel name every time you retest, and click Start conversation." -ForegroundColor White
Write-Host ""