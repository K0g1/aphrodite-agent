# Aphrodite Agent Installer for Windows
# Run: .\scripts\install.ps1

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

Write-Host "💎 Aphrodite Agent Installer" -ForegroundColor Magenta
Write-Host "   Platform: Windows $([System.Environment]::OSVersion.VersionString)" -ForegroundColor Gray
Write-Host ""

# Check Python
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $Python) {
    Write-Host "❌ Python not found. Install Python 3.11+ from https://python.org" -ForegroundColor Red
    exit 1
}

$PyVer = & $Python.Source --version 2>&1
$PyMatch = [regex]::Match($PyVer, '(\d+)\.(\d+)')
$PyMajor = [int]$PyMatch.Groups[1].Value
$PyMinor = [int]$PyMatch.Groups[2].Value

if ($PyMajor -lt 3 -or ($PyMajor -eq 3 -and $PyMinor -lt 11)) {
    Write-Host "❌ $PyVer found, but 3.11+ is required." -ForegroundColor Red
    exit 1
}

Write-Host "✅ $PyVer" -ForegroundColor Green

# Create venv
$VenvDir = Join-Path $ProjectDir ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "🔨 Creating virtual environment..." -ForegroundColor Cyan
    & $Python.Source -m venv $VenvDir
}

# Activate and install
$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
. $Activate

Write-Host "🔨 Upgrading pip..." -ForegroundColor Cyan
python -m pip install --quiet --upgrade pip

Write-Host "🔨 Installing Aphrodite Agent..." -ForegroundColor Cyan
pip install --quiet -e $ProjectDir

Write-Host "🔨 Installing dev dependencies..." -ForegroundColor Cyan
pip install --quiet -e "$ProjectDir[dev]"

# Config directory
$ConfigDir = Join-Path $env:USERPROFILE ".config\aphrodite-agent"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

$ConfigFile = Join-Path $ConfigDir "aphrodite.toml"
if (-not (Test-Path $ConfigFile)) {
    Write-Host "📝 Copying sample config..." -ForegroundColor Cyan
    Copy-Item (Join-Path $ProjectDir "aphrodite.toml") $ConfigFile
}

# Data directory
$DataDir = Join-Path $env:USERPROFILE ".local\share\aphrodite-agent"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Write-Host ""
Write-Host "✅ Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "   1. Edit config: $ConfigFile"
Write-Host "   2. Set your API key: `$env:OPENROUTER_API_KEY = 'sk-...'"
Write-Host "   3. Create a character: aphrodite create --character mira"
Write-Host "   4. Start chatting: aphrodite chat"
Write-Host "   5. Or launch web UI: aphrodite api"
Write-Host ""
Write-Host "Run 'aphrodite --help' for all commands." -ForegroundColor Gray
