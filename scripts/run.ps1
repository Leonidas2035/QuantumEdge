param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("supervisor", "bot", "meta")]
    [string]$Mode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPath = Join-Path $root ".venv"
$python = Join-Path $venvPath "Scripts\python.exe"
$cli = Join-Path $root "tools\qe_cli.py"

if (-not (Test-Path $python)) {
    Write-Host "[run] Missing .venv. Run scripts\setup.ps1 first."
    exit 1
}

$paths = @(
    $root,
    Join-Path $root "ai_scalper_bot",
    Join-Path $root "SupervisorAgent",
    Join-Path $root "meta_agent"
)
if ($env:PYTHONPATH) { $paths += $env:PYTHONPATH }
$env:PYTHONPATH = ($paths -join ";")
$env:QE_ROOT = $root

Write-Host "[run] Environment variables to set (do not commit secrets):"
Write-Host "  SCALPER_SECRETS_PASSPHRASE"
Write-Host "  BINANCE_API_KEY / BINANCE_API_SECRET"
Write-Host "  BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET"
Write-Host "  BINGX_API_KEY / BINGX_API_SECRET"
Write-Host "  BINGX_DEMO_API_KEY / BINGX_DEMO_API_SECRET"
Write-Host "  OPENAI_API_KEY / OPENAI_API_KEY_SUPERVISOR"
Write-Host "  OPENAI_API_KEY_DEV / OPENAI_API_KEY_PROD"

$subcommand = switch ($Mode) {
    "supervisor" { "supervisor" }
    "bot" { "bot" }
    "meta" { "meta" }
}

$configPath = switch ($Mode) {
    "supervisor" { Join-Path $root "config\supervisor.yaml" }
    "bot" { Join-Path $root "config\bot.yaml" }
    "meta" { Join-Path $root "config\meta_agent.yaml" }
}

& $python $cli $subcommand --config $configPath @args
