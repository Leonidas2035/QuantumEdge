param(
    [string]$ServiceName = "MetaAgentWatch",
    [string]$InstallDir = "C:\QuantumEdge",
    [string]$PythonExe = "C:\Python312\python.exe"
)

$nssm = Join-Path $InstallDir "tools\nssm\nssm.exe"
if (-not (Test-Path $nssm)) {
    Write-Host "nssm.exe not found at $nssm"
    exit 1
}

$exe = $PythonExe
$args = "meta_agent.py watch --inbox runtime/inbox --poll-seconds 2 --archive runtime/inbox_done --failed runtime/inbox_failed"

& $nssm install $ServiceName $exe $args
& $nssm set $ServiceName AppDirectory $InstallDir
& $nssm set $ServiceName AppEnvironmentExtra "META_AGENT_RUNTIME_DIR=$InstallDir\runtime" "META_AGENT_LOG_LEVEL=INFO"

Write-Host "Service $ServiceName installed."
