param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigHome = "$env:USERPROFILE\.zetherion-dev-agent",
    [Parameter(Mandatory = $false)]
    [string]$RuntimeRoot = "C:\ZetherionCI\agent-runtime",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "ci-worker-connectivity.json",
    [Parameter(Mandatory = $false)]
    [switch]$RunWorkerOnce,
    [Parameter(Mandatory = $false)]
    [switch]$ExerciseRelayFailover
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-AgentConfig {
    param(
        [string]$ConfigRoot,
        [string]$RuntimePath
    )

    $pythonExe = Join-Path $RuntimePath "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw "Unable to locate agent virtualenv Python under $RuntimePath"
    }

    $env:ZETHERION_DEV_AGENT_HOME = $ConfigRoot
    $code = @"
import json
from zetherion_dev_agent.config import AgentConfig

cfg = AgentConfig.load()
print(json.dumps({
    "worker_base_url": cfg.worker_base_url,
    "worker_relay_base_url": cfg.worker_relay_base_url,
    "worker_relay_secret": cfg.worker_relay_secret,
    "worker_scope_id": cfg.worker_scope_id,
    "worker_bootstrap_secret": bool(cfg.worker_bootstrap_secret),
    "worker_node_id": cfg.worker_node_id,
    "database_path": cfg.database_path,
}))
"@
    $output = & $pythonExe -c $code
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to load zetherion-dev-agent config"
    }
    return @{
        PythonExe = $pythonExe
        Data = ($output | ConvertFrom-Json)
    }
}

function Url-Port {
    param([uri]$Uri)
    if ($Uri.Port -gt 0) {
        return $Uri.Port
    }
    if ($Uri.Scheme -eq "https") {
        return 443
    }
    return 80
}

function Test-Endpoint {
    param(
        [string]$Url,
        [hashtable]$Headers = @{}
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -Method GET -Headers $Headers -UseBasicParsing -TimeoutSec 10
        return @{
            ok = $true
            status = [int]$response.StatusCode
            body = [string]$response.Content
        }
    }
    catch {
        $statusCode = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        return @{
            ok = $false
            status = $statusCode
            error = $_.Exception.Message
        }
    }
}

function Test-Route {
    param([string]$Url)
    $uri = [uri]$Url
    $hostName = $uri.Host
    $port = Url-Port -Uri $uri
    $dns = $null
    try {
        $dns = Resolve-DnsName -Name $hostName -ErrorAction Stop | Select-Object -First 1
    }
    catch {
        $dns = $null
    }
    $connection = Test-NetConnection -ComputerName $hostName -Port $port -WarningAction SilentlyContinue
    return @{
        host = $hostName
        port = $port
        dns_name = if ($dns) { $dns.NameHost } else { $null }
        dns_address = if ($dns) { $dns.IPAddress } else { $null }
        tcp_open = [bool]$connection.TcpTestSucceeded
    }
}

function Invoke-WorkerOnce {
    param(
        [string]$PythonExe,
        [string]$ConfigRoot,
        [hashtable]$Overrides = @{}
    )

    $env:ZETHERION_DEV_AGENT_HOME = $ConfigRoot
    $previous = @{}
    foreach ($name in $Overrides.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$Overrides[$name], "Process")
    }

    try {
        $output = & $PythonExe -m zetherion_dev_agent.cli worker --once 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        foreach ($name in $Overrides.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
        }
    }

    return @{
        exit_code = $exitCode
        output = @($output)
        claimed_job = [bool](@($output) -match "claimed_job=True")
        submitted = [bool](@($output) -match "status=(succeeded|failed)")
    }
}

$config = Read-AgentConfig -ConfigRoot $ConfigHome -RuntimePath $RuntimeRoot
$pythonExe = $config.PythonExe
$cfg = $config.Data

$workerBaseUrl = [string]$cfg.worker_base_url
$relayBaseUrl = [string]$cfg.worker_relay_base_url

$directHealthUrl = $workerBaseUrl.TrimEnd("/") + "/health"
$relayHealthUrl = if ($relayBaseUrl) { $relayBaseUrl.TrimEnd("/") } else { "" }

$directRoute = Test-Route -Url $directHealthUrl
$directHealth = Test-Endpoint -Url $directHealthUrl
$relayRoute = $null
$relayHealth = $null
if ($relayHealthUrl) {
    $relayRoute = Test-Route -Url $relayHealthUrl
    $relaySecret = [string]$cfg.worker_relay_secret
    $relayHeaders = @{}
    if ($relaySecret) {
        $relayHeaders["x-ci-relay-secret"] = $relaySecret
    }
    $relayHealth = Test-Endpoint -Url $relayHealthUrl -Headers $relayHeaders
}

$githubReachable = $false
try {
    $null = & gh auth status 2>$null
    $githubReachable = ($LASTEXITCODE -eq 0)
}
catch {
    $githubReachable = $false
}

$dockerReachable = $false
try {
    $null = & docker version --format "{{.Server.Version}}" 2>$null
    $dockerReachable = ($LASTEXITCODE -eq 0)
}
catch {
    $dockerReachable = $false
}

$workerOnce = $null
if ($RunWorkerOnce) {
    $workerOnce = Invoke-WorkerOnce -PythonExe $pythonExe -ConfigRoot $ConfigHome
}

$relayFailover = $null
if ($ExerciseRelayFailover -and $relayHealthUrl) {
    $relayFailover = Invoke-WorkerOnce `
        -PythonExe $pythonExe `
        -ConfigRoot $ConfigHome `
        -Overrides @{ "DEV_AGENT_WORKER_BASE_URL" = "http://127.0.0.1:9/owner/ci/worker/v1" }
}

$result = [ordered]@{
    installed = (Test-Path -LiteralPath (Join-Path $ConfigHome "config.toml"))
    bootstrapped = [bool]$cfg.worker_bootstrap_secret
    heartbeating = [bool]$directHealth.ok -or [bool]($relayHealth -and $relayHealth.ok)
    claiming = [bool]($workerOnce -and $workerOnce.claimed_job)
    submitting = [bool]($workerOnce -and $workerOnce.submitted)
    direct = @{
        route = $directRoute
        health = $directHealth
    }
    relay = if ($relayHealth) {
        @{
            route = $relayRoute
            health = $relayHealth
            failover = $relayFailover
        }
    }
    else {
        $null
    }
    github = @{
        reachable = $githubReachable
    }
    docker = @{
        reachable = $dockerReachable
    }
    worker_once = $workerOnce
    verified_at = (Get-Date).ToUniversalTime().ToString("o")
    note = "Claiming and submitting only flip true when a queued worker job is available during --RunWorkerOnce."
}

$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath
Write-Host "Connectivity receipt written to $OutputPath"
