<# Windows bootstrap script for Ollama CLI environment
   - Checks for Python, creates a per-user venv, installs dev deps
   - Tries to start Ollama server if available
   - Tries to start SeArxNG via Docker (if Docker available)
   - Sets necessary environment variables for run-time
   - Outputs next steps to run ollama-cli interactive --advanced
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== Ollama CLI: Automated Windows bootstrap ===" -ForegroundColor Cyan

$repoRoot = (Get-Location).Path
Write-Host "Repo root: $repoRoot"

# 1) Locate Python (user install preferred)
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  $py = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $py) {
  Write-Host "Python not found in PATH. Please install Python 3.14+ for user (no admin) install." -ForegroundColor Red
  exit 1
}
Write-Host "Python found: $($py.Path)" -ForegroundColor Green

# 2) Setup virtual environment using the Python found
$venvPath = ".\venv"
$venvPython = "${venvPath}\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Creating virtual environment at $venvPath" -ForegroundColor Yellow
  & $py.Path -m venv $venvPath
}
if (-not (Test-Path $venvPython)) {
  Write-Host "Failed to create Python virtual environment." -ForegroundColor Red
  exit 1
}
Write-Host "Using venv Python: $venvPython" -ForegroundColor Green

# 3) Install dependencies
& $venvPython -m pip install --upgrade pip setuptools wheel
& $venvPython -m pip install -e .
& $venvPython -m pip install pytest pytest-asyncio

# 4) Start Ollama server if available
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
  Write-Host "Attempting to start Ollama server..." -ForegroundColor Yellow
  try {
    & $ollamaCmd.Source serve
  } catch {
    Write-Host "Could not auto-start Ollama: $($_.Exception.Message)" -ForegroundColor Yellow
  }
} else {
  Write-Host "Ollama not found on PATH. Install Ollama to run the server automatically." -ForegroundColor Yellow
}

# 5) Start SearxNG (Docker) if available
if (Get-Command docker -ErrorAction SilentlyContinue) {
  $searxDir = "$repoRoot\searxng"  # optional directory for docker-compose
  if (-not (Test-Path $searxDir)) { New-Item -ItemType Directory -Path $searxDir | Out-Null }
  $composePath = "$searxDir\docker-compose.yml"
  if (-not (Test-Path $composePath)) {
    @'
version: 3.8
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    ports:
      - "8080:8080"
    volumes:
      - ./searxng-config/settings.yml:/etc/searxng/settings.yml
    restart: unless-stopped
'@ | Set-Content -Path $composePath -Encoding UTF8
  }
  $settingsDir = "$searxDir\searxng-config"
  if (-not (Test-Path $settingsDir)) { New-Item -ItemType Directory -Path $settingsDir | Out-Null }
  if (-not (Test-Path "$settingsDir\settings.yml")) {
    @'
search:
  formats:
    - html
    - json
server:
  limit_query: true
  default_query_params:
    format: json
'@ | Set-Content -Path "$settingsDir\settings.yml" -Encoding UTF8
  }
  Write-Host "Starting searxng via docker-compose..." -ForegroundColor Green
  Push-Location $searxDir
  docker-compose up -d
  Pop-Location
} else {
  Write-Host "Docker not found. Skipping searxng startup." -ForegroundColor Yellow
}

# 6) Configure environment variables for runtime
$OLLAMA_BASE = "http://localhost:11434"
$SEARXNG_URL = "http://localhost:8080/search"
setx OLLAMA_BASE_URL "$OLLAMA_BASE" | Out-Null
setx SEARXNG_URL "$SEARXNG_URL" | Out-Null
Write-Host "Environment configured: OLLAMA_BASE_URL=$OLLAMA_BASE, SEARXNG_URL=$SEARXNG_URL" -ForegroundColor Green

Write-Host "Bootstrap complete. Open a new PowerShell window and run: ollama-cli interactive --advanced" -ForegroundColor Cyan
