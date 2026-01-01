Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

param(
    [string]$EnvFile = "config\\secrets.local.env"
)

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$path = Join-Path $root $EnvFile
if (-not (Test-Path $path)) {
    Write-Error "[secrets] Missing env file: $path"
    exit 1
}

$loaded = @()
Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    if ($line.ToLower().StartsWith("export ")) {
        $line = $line.Substring(7).Trim()
    }
    $parts = $line.Split("=", 2)
    if ($parts.Length -ne 2) {
        return
    }
    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (-not $key) {
        return
    }
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $env:$key = $value
    $loaded += $key
}

if ($loaded.Count -eq 0) {
    Write-Warning "[secrets] No keys loaded from $path"
} else {
    Write-Host "[secrets] Loaded keys: $($loaded -join ', ')"
}

$required = @("BINGX_DEMO_API_KEY", "BINGX_DEMO_API_SECRET")
$missing = @()
foreach ($key in $required) {
    if (-not $env:$key) {
        $missing += $key
    }
}
if ($missing.Count -gt 0) {
    Write-Warning "[secrets] Missing required keys: $($missing -join ', ')"
}
