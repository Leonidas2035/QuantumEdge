$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..\\..")
$python = Join-Path $repo ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
    Write-Warning "Root .venv not found. Using system python."
    $python = "python"
}

& $python (Join-Path $repo "QuantumEdge.py") start @args
exit $LASTEXITCODE
