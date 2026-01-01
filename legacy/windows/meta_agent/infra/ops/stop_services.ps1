$ErrorActionPreference = "Stop"
$envPath = Join-Path $PSScriptRoot "windows.env"
$nssm = "nssm.exe"
if (Test-Path $envPath) {
    $cfg = @{}
    Get-Content $envPath | ForEach-Object {
        if (-not $_ -or $_.StartsWith("#") -or (-not $_.Contains("="))) { return }
        $parts = $_ -split "=", 2
        $cfg[$parts[0].Trim()] = $parts[1].Trim()
    }
    if ($cfg.ContainsKey("NSSM_EXE") -and $cfg["NSSM_EXE"]) { $nssm = $cfg["NSSM_EXE"] }
}
& $nssm stop QuantumEdgeBot
& $nssm stop SupervisorAgent
Write-Host "Services stopped."
