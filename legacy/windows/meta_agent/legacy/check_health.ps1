$ErrorActionPreference = "Stop"
param(
    [string]$SupervisorUrl = "http://localhost:8000/api/v1/dashboard/health"
)
try {
    $resp = Invoke-WebRequest -Uri $SupervisorUrl -UseBasicParsing -TimeoutSec 5
    $json = $resp.Content | ConvertFrom-Json
    Write-Host "Supervisor health:" $json.status
    if ($json.issues) { $json.issues | ForEach-Object { Write-Host " - $_" } }
} catch {
    Write-Warning "Health check failed: $_"
}
