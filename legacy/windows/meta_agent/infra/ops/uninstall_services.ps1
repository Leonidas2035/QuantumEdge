$ErrorActionPreference = "Stop"

function Load-Env($path) {
    if (-not (Test-Path $path)) { return @{} }
    $map = @{}
    Get-Content $path | ForEach-Object {
        if (-not $_ -or $_.StartsWith("#") -or (-not $_.Contains("="))) { return }
        $parts = $_ -split "=", 2
        $map[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $map
}

$envPath = Join-Path $PSScriptRoot "windows.env"
$cfg = Load-Env $envPath
$nssm = $cfg["NSSM_EXE"]
if (-not $nssm) { $nssm = "nssm.exe" }

function Remove-ServiceSafe($name) {
    try {
        & $nssm stop $name
    } catch {}
    try {
        & $nssm remove $name confirm
        Write-Host "Removed $name"
    } catch {
        Write-Warning "Failed to remove $name: $_"
    }
}

Remove-ServiceSafe "QuantumEdgeBot"
Remove-ServiceSafe "SupervisorAgent"
