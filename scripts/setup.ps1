Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$venvPath = Join-Path $root ".venv"
$python = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "[setup] Creating virtual environment at $venvPath"
    py -3 -m venv $venvPath
}

Write-Host "[setup] Upgrading pip"
& $python -m pip install --upgrade pip

Write-Host "[setup] Installing runtime dependencies"
& $python -m pip install -r (Join-Path $root "requirements\requirements.txt")

Write-Host "[setup] Installing dev/test dependencies"
& $python -m pip install -r (Join-Path $root "requirements\requirements-dev.txt")

Write-Host "[setup] Done. Next: use scripts\run.ps1 to launch components."
