$ErrorActionPreference = "Stop"

function Load-Env($path) {
    if (-not (Test-Path $path)) {
        throw "Env file not found: $path"
    }
    $map = @{}
    Get-Content $path | ForEach-Object {
        if (-not $_ -or $_.StartsWith("#") -or (-not $_.Contains("="))) { return }
        $parts = $_ -split "=", 2
        $map[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $map
}

function Get-Cfg($cfg, $key, $default = "") {
    if ($cfg.ContainsKey($key) -and $cfg[$key]) { return $cfg[$key] }
    return $default
}

$envPath = Join-Path $PSScriptRoot "windows.env"
$cfg = Load-Env $envPath

$python = Get-Cfg $cfg "PYTHON_EXE" "python.exe"
$qeRoot = Get-Cfg $cfg "QE_ROOT" (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..\\..")).Path
$botDir = Get-Cfg $cfg "AI_SCALPER_BOT_DIR" (Join-Path $qeRoot "ai_scalper_bot")
$supDir = Get-Cfg $cfg "SUPERVISOR_AGENT_DIR" (Join-Path $qeRoot "SupervisorAgent")
$botEntry = Get-Cfg $cfg "BOT_ENTRYPOINT" "run_bot.py --mode paper"
$supEntry = Get-Cfg $cfg "SUP_ENTRYPOINT" "supervisor.py run-foreground"
$nssm = Get-Cfg $cfg "NSSM_EXE" "nssm.exe"
$logDir = Get-Cfg $cfg "LOG_DIR" (Join-Path $qeRoot "logs")

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Install-Service($name, $workdir, $entry) {
    $stdout = Join-Path $logDir "$name-stdout.log"
    $stderr = Join-Path $logDir "$name-stderr.log"
    & $nssm install $name $python $entry
    & $nssm set $name AppDirectory $workdir
    & $nssm set $name AppStdout $stdout
    & $nssm set $name AppStderr $stderr
    & $nssm set $name AppRotateFiles 1
    & $nssm set $name AppRotateBytes 10485760
    & $nssm set $name Start SERVICE_AUTO_START
    & $nssm set $name AppRestartDelay 3000
    Write-Host "Installed service $name"
}

Install-Service "QuantumEdgeBot" $botDir $botEntry
Install-Service "SupervisorAgent" $supDir $supEntry

Write-Host "Services installed. Use start_services.ps1 to start."
