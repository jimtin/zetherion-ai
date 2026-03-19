param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigHome = "$env:USERPROFILE\.zetherion-dev-agent",
    [Parameter(Mandatory = $false)]
    [string]$RuntimeRoot = "C:\ZetherionCI\agent-runtime",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "ci-worker-connectivity.json",
    [Parameter(Mandatory = $false)]
    [string]$WslKeepaliveTaskName = "ZetherionWslKeepalive",
    [Parameter(Mandatory = $false)]
    [switch]$RunWorkerOnce,
    [Parameter(Mandatory = $false)]
    [switch]$ExerciseRelayFailover
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

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
    $code = @'
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
    "worker_execution_backend": cfg.worker_execution_backend,
    "worker_workspace_root": cfg.worker_workspace_root,
    "worker_runtime_root": cfg.worker_runtime_root,
    "worker_docker_backend": cfg.worker_docker_backend,
    "worker_wsl_distribution": cfg.worker_wsl_distribution,
    "worker_allowed_repo_roots": cfg.worker_allowed_repo_roots,
    "worker_denied_repo_roots": cfg.worker_denied_repo_roots,
    "database_path": cfg.database_path,
}))
'@
    $output = $code | & $pythonExe -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to load zetherion-dev-agent config"
    }
    return @{
        PythonExe = $pythonExe
        Data = ($output | ConvertFrom-Json)
    }
}

function ConvertFrom-ZetherionJson {
    param(
        [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
        [string]$InputObject
    )

    process {
        $command = Get-Command ConvertFrom-Json -ErrorAction Stop
        if ($command.Parameters.ContainsKey("Depth")) {
            return ($InputObject | ConvertFrom-Json -Depth 32)
        }
        return ($InputObject | ConvertFrom-Json)
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
    $dnsName = $null
    $dnsAddress = $null
    if ($dns) {
        if ($dns.PSObject.Properties.Name -contains "NameHost") {
            $dnsName = $dns.NameHost
        }
        if ($dns.PSObject.Properties.Name -contains "IPAddress") {
            $dnsAddress = $dns.IPAddress
        }
    }
    $connection = Test-NetConnection -ComputerName $hostName -Port $port -WarningAction SilentlyContinue
    return @{
        host = $hostName
        port = $port
        dns_name = $dnsName
        dns_address = $dnsAddress
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

function Get-ScheduledTaskSummary {
    param([string]$TaskName)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return @{
            exists = $false
            state = "Missing"
        }
    }

    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    return @{
        exists = $true
        state = [string]$task.State
        principal_user = [string]$task.Principal.UserId
        last_task_result = if ($info) { [int]$info.LastTaskResult } else { $null }
        last_run_time = if ($info) { $info.LastRunTime } else { $null }
    }
}

function Resolve-WorkspaceEvidencePath {
    param(
        [string]$RepoRoot,
        [string]$Candidate
    )

    $raw = [string]$Candidate
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return ""
    }

    if ($raw -like "/workspace/*") {
        $relative = $raw.Substring("/workspace".Length).TrimStart("/", "\")
        return Join-Path $RepoRoot $relative
    }
    if ($raw -eq "/workspace") {
        return $RepoRoot
    }
    if ($raw -match '^[A-Za-z]:[\\/]') {
        return $raw
    }
    if ([System.IO.Path]::IsPathRooted($raw)) {
        return $raw
    }
    return Join-Path $RepoRoot $raw
}

function Get-LatestWorkspaceReadiness {
    param([string]$WorkspaceRoot)

    if ([string]::IsNullOrWhiteSpace($WorkspaceRoot) -or -not (Test-Path -LiteralPath $WorkspaceRoot)) {
        return $null
    }

    $receipts = @()
    foreach ($repoDir in Get-ChildItem -LiteralPath $WorkspaceRoot -Directory -ErrorAction SilentlyContinue) {
        foreach ($candidate in @(
            (Join-Path $repoDir.FullName ".artifacts\local-readiness-receipt.json"),
            (Join-Path $repoDir.FullName ".ci\local-readiness-receipt.json")
        )) {
            if (-not (Test-Path -LiteralPath $candidate)) {
                continue
            }

            try {
                $payload = Get-Content -LiteralPath $candidate -Raw | ConvertFrom-ZetherionJson
            }
            catch {
                continue
            }

            $recordedAt = $null
            try {
                $recordedAt = [datetimeoffset]::Parse([string]$payload.recorded_at)
            }
            catch {
                $recordedAt = [datetimeoffset](Get-Item -LiteralPath $candidate).LastWriteTimeUtc
            }

            $cleanupPaths = @()
            $cleanupVerified = $false
            $cleanupStatuses = @()
            $shardReceipts = @()
            if ($payload.PSObject.Properties.Name -contains "shard_receipts") {
                $shardReceipts = @($payload.shard_receipts)
            }
            foreach ($shard in $shardReceipts) {
                if (-not $shard) {
                    continue
                }
                $cleanupCandidate = ""
                if ($shard.result -and $shard.result.cleanup_receipt_path) {
                    $cleanupCandidate = [string]$shard.result.cleanup_receipt_path
                }
                elseif ($shard.metadata -and $shard.metadata.cleanup_receipt_path) {
                    $cleanupCandidate = [string]$shard.metadata.cleanup_receipt_path
                }
                $resolvedCleanup = Resolve-WorkspaceEvidencePath -RepoRoot $repoDir.FullName -Candidate $cleanupCandidate
                if ([string]::IsNullOrWhiteSpace($resolvedCleanup)) {
                    continue
                }
                $cleanupPaths += $resolvedCleanup
                if (-not (Test-Path -LiteralPath $resolvedCleanup)) {
                    continue
                }
                try {
                    $cleanupPayload = Get-Content -LiteralPath $resolvedCleanup -Raw | ConvertFrom-ZetherionJson
                    $cleanupStatuses += [string]$cleanupPayload.status
                }
                catch {
                    $cleanupStatuses += "parse_failed"
                }
            }

            if ($cleanupPaths.Count -gt 0) {
                $cleanupVerified = (@($cleanupStatuses | Where-Object { $_ -in @("clean", "cleaned") }).Count -eq $cleanupPaths.Count)
            }

            $statusValue = [string]$payload.status
            $mergeReady = [bool]$payload.merge_ready
            $deployReady = [bool]$payload.deploy_ready
            $ciTestSucceeded = (
                $shardReceipts -and
                (($statusValue -in @("success", "healthy")) -or $mergeReady -or $deployReady)
            )

            $receipts += @{
                repo_id = [string]$payload.repo_id
                path = $candidate
                repo_root = $repoDir.FullName
                recorded_at = $recordedAt
                payload = $payload
                ci_test_run_succeeded = $ciTestSucceeded
                cleanup_verified = $cleanupVerified
                cleanup_paths = @($cleanupPaths)
                cleanup_statuses = @($cleanupStatuses)
            }
        }
    }

    if (-not $receipts) {
        return $null
    }

    return $receipts | Sort-Object -Property recorded_at -Descending | Select-Object -First 1
}

$config = Read-AgentConfig -ConfigRoot $ConfigHome -RuntimePath $RuntimeRoot
$pythonExe = $config.PythonExe
$cfg = $config.Data

$workerBaseUrl = [string]$cfg.worker_base_url
$relayBaseUrl = [string]$cfg.worker_relay_base_url
$executionBackend = [string]$cfg.worker_execution_backend
$workspaceRoot = [string]$cfg.worker_workspace_root
$dockerBackend = [string]$cfg.worker_docker_backend
$wslDistribution = [string]$cfg.worker_wsl_distribution
$env:ZETHERION_WSL_DISTRIBUTION = $wslDistribution

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

$dockerStatus = Get-ZetherionDockerRuntimeStatus `
    -ExecutionBackend $executionBackend `
    -DockerBackend $dockerBackend
$dockerReachable = [bool]$dockerStatus.available
$wslStatus = $null
$wslHostConfig = $null
$wslDockerConfig = $null
$workspaceReadiness = Get-LatestWorkspaceReadiness -WorkspaceRoot $workspaceRoot
$wslKeepaliveTask = Get-ScheduledTaskSummary -TaskName $WslKeepaliveTaskName
if ($executionBackend -eq "wsl_docker" -or $dockerBackend -eq "wsl_docker") {
    $wslHostConfig = Get-ZetherionWslHostConfig
    $wslDockerConfig = Ensure-ZetherionWslDockerHeadlessConfig
    $wslProbe = Invoke-ZetherionWslCommandResult -Command "uname -a && systemctl is-active docker"
    $wslStatus = @{
        distribution = $wslDistribution
        reachable = ($wslProbe.ExitCode -eq 0)
        output = @($wslProbe.Output)
    }
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
        -Overrides @{ "DEV_AGENT_WORKER_BASE_URL" = "https://127.0.0.1:9/owner/ci/worker/v1" }
}

$heartbeating = [bool]$directHealth.ok -or [bool]($relayHealth -and $relayHealth.ok)
$claiming = [bool]($workerOnce -and $workerOnce.claimed_job)
$submitting = [bool]($workerOnce -and $workerOnce.submitted)
$statusPublicationSucceeded = [bool]$githubReachable
$ciTestRunSucceeded = [bool]($workspaceReadiness -and $workspaceReadiness.ci_test_run_succeeded)
$cleanupVerified = [bool]($workspaceReadiness -and $workspaceReadiness.cleanup_verified)
$workerNoopSucceeded = [bool](
    $workerOnce -and
    $workerOnce.claimed_job -and
    $workerOnce.submitted -and
    (@($workerOnce.output) -match "status=succeeded")
)
$wslKeepaliveHealthy = [bool](
    $wslKeepaliveTask -and
    $wslKeepaliveTask.exists -and
    (
        $wslKeepaliveTask.state -eq "Running" -or
        (
            $wslKeepaliveTask.last_task_result -eq 0 -and
            $wslStatus -and [bool]$wslStatus.reachable -and
            $dockerReachable -and
            $wslHostConfig -and [bool]$wslHostConfig.passes_idle_timeout
        )
    )
)
$checks = @(
    @{
        key = "bootstrap_succeeded"
        label = "Bootstrap"
        status = if ([bool]$cfg.worker_bootstrap_secret) { "passed" } else { "failed" }
        summary = "Worker bootstrap secret is configured."
        blocker = $true
    },
    @{
        key = "registration_succeeded"
        label = "Registration"
        status = if ($heartbeating) { "passed" } else { "failed" }
        summary = "Worker registration is inferred from a healthy runtime endpoint."
        blocker = $true
    },
    @{
        key = "heartbeat_succeeded"
        label = "Heartbeat"
        status = if ($heartbeating) { "passed" } else { "failed" }
        summary = "Worker health endpoint is reachable directly or through relay."
        blocker = $true
    },
    @{
        key = "job_claim_succeeded"
        label = "Job claim"
        status = if ($RunWorkerOnce) { if ($claiming) { "passed" } elseif ($submitting) { "failed" } else { "pending" } } else { "pending" }
        summary = "Requires a queued worker job when --RunWorkerOnce is used."
        blocker = $true
    },
    @{
        key = "noop_job_succeeded"
        label = "worker.noop"
        status = if ($RunWorkerOnce) { if ($workerNoopSucceeded) { "passed" } elseif ($claiming) { "failed" } else { "pending" } } else { "pending" }
        summary = "Requires a queued worker.noop job during --RunWorkerOnce."
        blocker = $true
    },
    @{
        key = "ci_test_run_succeeded"
        label = "ci.test.run"
        status = if ($ciTestRunSucceeded) { "passed" } else { "pending" }
        summary = if ($ciTestRunSucceeded) {
            "A workspace readiness receipt confirms a real ci.test.run shard succeeded."
        } else {
            "Run a real ci.test.run shard to complete native-worker certification."
        }
        blocker = $true
    },
    @{
        key = "artifacts_submitted"
        label = "Artifacts submitted"
        status = if ($RunWorkerOnce) { if ($submitting) { "passed" } else { "pending" } } else { "pending" }
        summary = "Artifacts/log submission is exercised when --RunWorkerOnce claims a job."
        blocker = $true
    },
    @{
        key = "cleanup_verified"
        label = "Cleanup verified"
        status = if ($cleanupVerified) { "passed" } else { "pending" }
        summary = if ($cleanupVerified) {
            "Workspace cleanup receipts exist and report clean teardown."
        } else {
            "Requires a real ci.test.run shard with cleanup receipts."
        }
        blocker = $true
    },
    @{
        key = "status_publication_succeeded"
        label = "Status publication"
        status = if ($statusPublicationSucceeded) { "passed" } else { "failed" }
        summary = "GitHub CLI authentication is required to publish external statuses."
        blocker = $false
    },
    @{
        key = "wsl_keepalive_task_running"
        label = "WSL keepalive"
        status = if ($wslKeepaliveHealthy) { "passed" } elseif ($wslKeepaliveTask.exists) { "failed" } else { "failed" }
        summary = if ($wslKeepaliveHealthy) {
            "The WSL executor is being kept alive either by an active keepalive task or by a successful startup keepalive run combined with verified WSL idle-timeout headroom."
        } else {
            "A dedicated keepalive task must hold the Ubuntu WSL executor open for unattended Docker availability."
        }
        blocker = $true
    },
    @{
        key = "wsl_idle_timeout_configured"
        label = "WSL idle timeout"
        status = if ($wslHostConfig) { if ([bool]$wslHostConfig.passes_idle_timeout) { "passed" } else { "failed" } } else { "pending" }
        summary = "Host WSL vmIdleTimeout must be configured high enough to keep the Docker executor alive without an attached client."
        blocker = [bool]$wslHostConfig
    },
    @{
        key = "wsl_docker_config_ready"
        label = "WSL Docker config"
        status = if ($wslDockerConfig) { if ([bool]$wslDockerConfig.headless_ready) { "passed" } else { "failed" } } else { "pending" }
        summary = "WSL Docker must not depend on Docker Desktop credential helpers for unattended builds."
        blocker = [bool]$wslDockerConfig
    }
)
$blockerCount = @($checks | Where-Object { $_.blocker -and $_.status -eq "failed" }).Count
$degradedCount = @($checks | Where-Object { $_.status -eq "degraded" }).Count
$status = if ($blockerCount -gt 0) {
    "failed"
} elseif (@($checks | Where-Object { $_.status -eq "pending" }).Count -gt 0) {
    "pending"
} elseif ($degradedCount -gt 0) {
    "degraded"
} else {
    "healthy"
}

$result = [ordered]@{
    receipt_kind = "WorkerCertificationReceipt"
    status = $status
    summary = if ($status -eq "healthy") {
        "Worker connectivity and certification checks passed."
    } elseif ($status -eq "failed") {
        "Worker connectivity has blocking failures."
    } else {
        "Worker connectivity is healthy, but job certification is incomplete."
    }
    execution_backend = $executionBackend
    workspace_root = $workspaceRoot
    runtime_root = [string]$cfg.worker_runtime_root
    docker_backend = $dockerBackend
    wsl_distribution = $wslDistribution
    required_checks = $checks
    blocker_count = $blockerCount
    degraded_count = $degradedCount
    installed = (Test-Path -LiteralPath (Join-Path $ConfigHome "config.toml"))
    bootstrapped = [bool]$cfg.worker_bootstrap_secret
    heartbeating = $heartbeating
    claiming = $claiming
    submitting = $submitting
    bootstrap_succeeded = [bool]$cfg.worker_bootstrap_secret
    registration_succeeded = $heartbeating
    heartbeat_succeeded = $heartbeating
    job_claim_succeeded = if ($RunWorkerOnce) { $claiming } else { $null }
    noop_job_succeeded = if ($RunWorkerOnce) { $workerNoopSucceeded } else { $null }
    ci_test_run_succeeded = $ciTestRunSucceeded
    artifacts_submitted = if ($RunWorkerOnce) { $submitting } else { $null }
    cleanup_verified = $cleanupVerified
    status_publication_succeeded = $statusPublicationSucceeded
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
        backend = [string]$dockerStatus.backend
        distribution = [string]$dockerStatus.distribution
        enabled = [bool]$dockerStatus.enabled
        active = [bool]$dockerStatus.active
    }
    wsl = $wslStatus
    wsl_keepalive_task = $wslKeepaliveTask
    wsl_host_config = if ($wslHostConfig) {
        @{
            path = [string]$wslHostConfig.path
            exists = [bool]$wslHostConfig.exists
            vm_idle_timeout_ms = $wslHostConfig.vm_idle_timeout_ms
            recommended_vm_idle_timeout_ms = $wslHostConfig.recommended_vm_idle_timeout_ms
            passes_idle_timeout = [bool]$wslHostConfig.passes_idle_timeout
        }
    }
    else {
        $null
    }
    wsl_docker_config = if ($wslDockerConfig) {
        @{
            path = [string]$wslDockerConfig.path
            exists = [bool]$wslDockerConfig.exists
            reachable = [bool]$wslDockerConfig.reachable
            changed = [bool]$wslDockerConfig.changed
            repaired = [bool]$wslDockerConfig.repaired
            valid_json = [bool]$wslDockerConfig.valid_json
            parse_error = [string]$wslDockerConfig.parse_error
            backup_path = [string]$wslDockerConfig.backup_path
            creds_store = [string]$wslDockerConfig.creds_store
            removed_desktop_helper = [bool]$wslDockerConfig.removed_desktop_helper
            removed_cred_helpers = @($wslDockerConfig.removed_cred_helpers)
            headless_ready = [bool]$wslDockerConfig.headless_ready
        }
    }
    else {
        $null
    }
    worker = @{
        allowed_repo_roots = @($cfg.worker_allowed_repo_roots)
        denied_repo_roots = @($cfg.worker_denied_repo_roots)
    }
    workspace_local_readiness = if ($workspaceReadiness) {
        @{
            repo_id = [string]$workspaceReadiness.repo_id
            path = [string]$workspaceReadiness.path
            repo_root = [string]$workspaceReadiness.repo_root
            ci_test_run_succeeded = [bool]$workspaceReadiness.ci_test_run_succeeded
            cleanup_verified = [bool]$workspaceReadiness.cleanup_verified
            cleanup_paths = @($workspaceReadiness.cleanup_paths)
            cleanup_statuses = @($workspaceReadiness.cleanup_statuses)
        }
    }
    else {
        $null
    }
    worker_once = $workerOnce
    verified_at = (Get-Date).ToUniversalTime().ToString("o")
    note = "Claiming, worker.noop, ci.test.run, artifacts, and cleanup only complete when queued jobs are available during --RunWorkerOnce."
}

$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath
Write-Host "Connectivity receipt written to $OutputPath"
