# Setup script for ai_scalper_bot (Windows, PowerShell)
# Creates venv, installs deps, and verifies bundled Python installer checksum if present.

Write-Host "`n=== AI Scalper Bot setup ===" -ForegroundColor Cyan

$BotPath = "C:\ai_scalper_bot"
if (-not (Test-Path $BotPath)) {
    Write-Host "Bot path not found: $BotPath" -ForegroundColor Red
    exit 1
}
Set-Location $BotPath

# Keep policy change scoped to this process only
Write-Host "Checking execution policy (process scope)..." -ForegroundColor Yellow
try {
    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force -ErrorAction Stop
} catch {
    Write-Host "ExecutionPolicy not changed (continuing): $($_.Exception.Message)" -ForegroundColor Yellow
}

# Optional: verify bundled Python installer checksum
$PyInstallerPath = Join-Path $BotPath "python-3.12.10-amd64.exe"
$ExpectedPyHash = "67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB"
if (Test-Path $PyInstallerPath) {
    try {
        $hash = (Get-FileHash $PyInstallerPath -Algorithm SHA256).Hash
        if ($hash -ne $ExpectedPyHash) {
            Write-Host "WARNING: Python installer hash mismatch! expected=$ExpectedPyHash actual=$hash" -ForegroundColor Red
        } else {
            Write-Host "Python installer checksum OK." -ForegroundColor Green
        }
    } catch {
        Write-Host "Checksum verification failed (continuing): $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# Python version check
Write-Host "Checking Python installation..." -ForegroundColor Yellow
$pinfo = & python -V 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Python not available. Install Python 3.12.x." -ForegroundColor Red
    exit 1
}
Write-Host "Python found: $pinfo" -ForegroundColor Green
if ($pinfo -notmatch "3\.12") {
    Write-Host "Python 3.12.x required (current: $pinfo)" -ForegroundColor Red
    exit 1
}

# Create venv
$VenvPath = "$BotPath\venv"
$PythonVenv = "$VenvPath\Scripts\python.exe"
if (-not (Test-Path $PythonVenv)) {
    Write-Host "Creating venv..." -ForegroundColor Yellow
    python -m venv venv
} else {
    Write-Host "Venv already exists." -ForegroundColor Green
}

# Activate venv (cmd to avoid policy issues)
Write-Host "Activating venv..." -ForegroundColor Yellow
cmd /c "$VenvPath\Scripts\activate.bat"

# Upgrade pip and install deps
Write-Host "Installing dependencies..." -ForegroundColor Yellow
$packages = @(
    "PyYAML>=6.0",
    "websockets>=11.0",
    "python-dotenv>=1.0",
    "aiohttp>=3.8",
    "numpy>=1.25",
    "pandas>=2.2",
    "python-binance>=1.0",
    "psutil",
    "pydantic",
    "requests",
    "xgboost",
    "torch --index-url https://download.pytorch.org/whl/cpu"
)
& $PythonVenv -m pip install --upgrade pip setuptools wheel
foreach ($pkg in $packages) {
    Write-Host "Installing $pkg" -ForegroundColor Gray
    & $PythonVenv -m pip install $pkg
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "To start: activate venv then run: python run_bot.py" -ForegroundColor Cyan
