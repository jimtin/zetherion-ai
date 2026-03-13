param(
    [Parameter(Mandatory = $false)]
    [string]$RepositoryUrl = "https://github.com/jimtin/zetherion-ai.git",
    [Parameter(Mandatory = $false)]
    [string]$Branch = "main",
    [Parameter(Mandatory = $false)]
    [string]$CheckoutRoot = "C:\ZetherionCI\agent-src",
    [Parameter(Mandatory = $false)]
    [string]$RuntimeRoot = "C:\ZetherionCI\agent-runtime",
    [Parameter(Mandatory = $false)]
    [string]$WorkspaceRoot = "C:\ZetherionCI\workspaces",
    [Parameter(Mandatory = $false)]
    [string]$ConfigHome = "$env:USERPROFILE\.zetherion-dev-agent",
    [Parameter(Mandatory = $true)]
    [string]$WorkerBaseUrl,
    [Parameter(Mandatory = $false)]
    [string]$RelayBaseUrl = "",
    [Parameter(Mandatory = $true)]
    [string]$ScopeId,
    [Parameter(Mandatory = $true)]
    [string]$WorkerBootstrapSecret,
    [Parameter(Mandatory = $false)]
    [string]$RelaySecret = "",
    [Parameter(Mandatory = $false)]
    [string]$NodeId = $env:COMPUTERNAME.ToLowerInvariant(),
    [Parameter(Mandatory = $false)]
    [string]$NodeName = $env:COMPUTERNAME,
    [Parameter(Mandatory = $false)]
    [string[]]$AllowedRepoRoots = @(),
    [Parameter(Mandatory = $false)]
    [string[]]$DeniedRepoRoots = @("C:\ZetherionAI"),
    [Parameter(Mandatory = $false)]
    [string[]]$AllowedActions = @("worker.noop", "ci.test.run", "repo.patch", "repo.commit", "repo.pr.open"),
    [Parameter(Mandatory = $false)]
    [string[]]$AllowedCommands = @("git", "python", "python3", "pytest", "ruff", "bash", "sh", "yarn", "node", "npm", "npx", "gitleaks", "docker", "docker-compose", "pwsh", "powershell"),
    [Parameter(Mandatory = $false)]
    [string[]]$Capabilities = @("ci.test.run"),
    [Parameter(Mandatory = $false)]
    [string[]]$ClaimCapabilities = @("ci.test.run"),
    [Parameter(Mandatory = $false)]
    [ValidateSet("native_windows_docker", "wsl_docker")]
    [string]$ExecutionBackend = "wsl_docker",
    [Parameter(Mandatory = $false)]
    [string]$DockerBackend = "wsl_docker",
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu",
    [Parameter(Mandatory = $false)]
    [int64]$RecommendedWslVmIdleTimeoutMs = 604800000,
    [Parameter(Mandatory = $false)]
    [int]$MaxRuntimeSeconds = 1800,
    [Parameter(Mandatory = $false)]
    [int]$MaxMemoryMb = 1024,
    [Parameter(Mandatory = $false)]
    [int]$MaxArtifactBytes = 1048576,
    [Parameter(Mandatory = $false)]
    [string]$TaskName = "ZetherionOwnerCiWorker",
    [Parameter(Mandatory = $false)]
    [string]$WslKeepaliveTaskName = "ZetherionWslKeepalive",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "ci-worker-install.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Escape-TomlString {
    param([string]$Value)
    $escaped = $Value.Replace("\", "\\").Replace('"', '\"')
    return '"' + $escaped + '"'
}

function Format-TomlStringArray {
    param([string[]]$Values)
    $items = @($Values | Where-Object { $_ -and $_.Trim() } | ForEach-Object { Escape-TomlString $_.Trim() })
    if ($items.Count -eq 0) {
        return "[]"
    }
    return "[" + ($items -join ", ") + "]"
}

function Resolve-PythonLauncher {
    $candidates = @(
        @{ Command = "py"; PrefixArgs = @("-3.12"); ProbeArgs = @("-c", "print('ok')") },
        @{ Command = "py"; PrefixArgs = @("-3.11"); ProbeArgs = @("-c", "print('ok')") },
        @{ Command = "py"; PrefixArgs = @("-3"); ProbeArgs = @("-c", "print('ok')") },
        @{ Command = "python"; PrefixArgs = @(); ProbeArgs = @("-c", "print('ok')") }
    )

    foreach ($candidate in $candidates) {
        try {
            $null = & $candidate.Command @($candidate.PrefixArgs) @($candidate.ProbeArgs) 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }

    throw "Unable to resolve a Python launcher. Install Python 3.11+ before running this script."
}

function Write-ConfigFile {
    param(
        [string]$Path,
        [string]$DatabasePath,
        [string]$LogDir
    )

    $lines = @(
        'webhook_url = ""',
        'agent_name = "zetherion-dev-agent"',
        "repos = []",
        "scan_interval = 60",
        "claude_code_enabled = false",
        "annotations_enabled = false",
        "git_enabled = false",
        "container_monitor_enabled = false",
        "cleanup_enabled = true",
        "cleanup_hour = 2",
        "cleanup_minute = 30",
        "approval_reprompt_hours = 24",
        'api_host = "127.0.0.1"',
        "api_port = 8787",
        'api_token = ""',
        ("database_path = " + (Escape-TomlString $DatabasePath)),
        'bootstrap_secret = ""',
        "bootstrap_require_once = true",
        ("worker_base_url = " + (Escape-TomlString $WorkerBaseUrl)),
        ("worker_relay_base_url = " + (Escape-TomlString $RelayBaseUrl)),
        ("worker_relay_secret = " + (Escape-TomlString $RelaySecret)),
        'worker_control_plane = "owner_ci"',
        ("worker_scope_id = " + (Escape-TomlString $ScopeId)),
        'worker_tenant_id = ""',
        ("worker_node_id = " + (Escape-TomlString $NodeId)),
        ("worker_node_name = " + (Escape-TomlString $NodeName)),
        ("worker_bootstrap_secret = " + (Escape-TomlString $WorkerBootstrapSecret)),
        ("worker_capabilities = " + (Format-TomlStringArray $Capabilities)),
        ("worker_claim_required_capabilities = " + (Format-TomlStringArray $ClaimCapabilities)),
        "worker_poll_after_seconds = 15",
        "worker_heartbeat_interval_seconds = 30",
        'worker_runner = "docker"',
        ("worker_execution_backend = " + (Escape-TomlString $ExecutionBackend)),
        ("worker_workspace_root = " + (Escape-TomlString $WorkspaceRoot)),
        ("worker_runtime_root = " + (Escape-TomlString $RuntimeRoot)),
        ("worker_docker_backend = " + (Escape-TomlString $DockerBackend)),
        ("worker_wsl_distribution = " + (Escape-TomlString $WslDistribution)),
        "worker_cleanup_enabled = true",
        "worker_cleanup_low_disk_free_bytes = 21474836480",
        "worker_cleanup_target_free_bytes = 42949672960",
        "worker_cleanup_artifact_retention_hours = 24",
        "worker_cleanup_log_retention_days = 7",
        ("worker_allowed_repo_roots = " + (Format-TomlStringArray $AllowedRepoRoots)),
        ("worker_denied_repo_roots = " + (Format-TomlStringArray $DeniedRepoRoots)),
        ("worker_allowed_actions = " + (Format-TomlStringArray $AllowedActions)),
        ("worker_allowed_commands = " + (Format-TomlStringArray $AllowedCommands)),
        "worker_max_runtime_seconds = $MaxRuntimeSeconds",
        "worker_max_memory_mb = $MaxMemoryMb",
        "worker_max_artifact_bytes = $MaxArtifactBytes",
        ("worker_log_dir = " + (Escape-TomlString $LogDir))
    )

    Set-Content -LiteralPath $Path -Value ($lines -join "`n")
}

function Register-WorkerTask {
    param(
        [string]$WrapperPath
    )

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WrapperPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType S4U `
        -RunLevel Highest

    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal

    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
}

function Register-WslKeepaliveTask {
    param(
        [string]$WrapperPath
    )

    $existing = Get-ScheduledTask -TaskName $WslKeepaliveTaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $WslKeepaliveTaskName -Confirm:$false
    }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WrapperPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType S4U `
        -RunLevel Highest

    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal

    Register-ScheduledTask -TaskName $WslKeepaliveTaskName -InputObject $task -Force | Out-Null
}

function Test-PathUnderLiveRuntime {
    param([string]$Path)

    $normalized = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    return $normalized -ieq "C:\ZetherionAI" -or $normalized.StartsWith("C:\ZetherionAI\", [System.StringComparison]::OrdinalIgnoreCase)
}

if (Test-PathUnderLiveRuntime -Path $WorkspaceRoot) {
    throw "WorkspaceRoot must not point to C:\ZetherionAI. Use C:\ZetherionCI\workspaces for worker execution."
}

if (Test-PathUnderLiveRuntime -Path $CheckoutRoot) {
    throw "CheckoutRoot must not point to C:\ZetherionAI."
}

Ensure-Directory -Path $CheckoutRoot
Ensure-Directory -Path $RuntimeRoot
Ensure-Directory -Path $WorkspaceRoot
Ensure-Directory -Path $ConfigHome

if ($AllowedRepoRoots.Count -eq 0) {
    $AllowedRepoRoots = @($WorkspaceRoot)
}

if ($PSBoundParameters.ContainsKey("DockerBackend") -eq $false) {
    $DockerBackend = $ExecutionBackend
}

if ($ExecutionBackend -eq "wsl_docker" -and -not ($AllowedCommands -contains "wsl")) {
    $AllowedCommands += "wsl"
}

$wslHostConfig = $null
$wslConfigChanged = $false
if ($ExecutionBackend -eq "wsl_docker" -or $DockerBackend -eq "wsl_docker") {
    $wslHostConfig = Get-ZetherionWslHostConfig
    $currentVmIdleTimeoutMs = if ($null -ne $wslHostConfig.vm_idle_timeout_ms) {
        [int64]$wslHostConfig.vm_idle_timeout_ms
    }
    else {
        -1
    }
    if (-not $wslHostConfig.exists -or $currentVmIdleTimeoutMs -lt $RecommendedWslVmIdleTimeoutMs) {
        $wslHostConfig = Set-ZetherionWslHostVmIdleTimeout -VmIdleTimeoutMs $RecommendedWslVmIdleTimeoutMs
        $wslConfigChanged = $true
    }
}

if (-not (Test-Path -LiteralPath $CheckoutRoot)) {
    throw "Checkout path does not exist after creation: $CheckoutRoot"
}

if (Test-Path -LiteralPath (Join-Path $CheckoutRoot ".git")) {
    Push-Location $CheckoutRoot
    try {
        git fetch --all --prune
        git checkout $Branch
        git pull --ff-only origin $Branch
    }
    finally {
        Pop-Location
    }
}
else {
    Remove-Item -LiteralPath $CheckoutRoot -Force -Recurse -ErrorAction SilentlyContinue
    git clone --branch $Branch --single-branch $RepositoryUrl $CheckoutRoot
}

$launcher = Resolve-PythonLauncher
$venvPath = Join-Path $RuntimeRoot "venv"
if (-not (Test-Path -LiteralPath $venvPath)) {
    & $launcher.Command @($launcher.PrefixArgs) "-m" "venv" $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at $venvPath"
    }
}

$pythonExe = Join-Path $venvPath "Scripts\python.exe"
$pipExe = Join-Path $venvPath "Scripts\pip.exe"
$cliExe = Join-Path $venvPath "Scripts\zetherion-dev-agent.exe"
$databasePath = Join-Path $ConfigHome "daemon.db"
$logDir = Join-Path $ConfigHome "worker-jobs"
$configPath = Join-Path $ConfigHome "config.toml"
$wrapperPath = Join-Path $RuntimeRoot "run-ci-worker.ps1"
$wslKeepaliveScriptPath = Join-Path $CheckoutRoot "scripts\windows\wsl-keepalive.ps1"
$wslKeepaliveWrapperPath = Join-Path $RuntimeRoot "run-wsl-keepalive.ps1"

& $pythonExe -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in $venvPath"
}

& $pipExe install -e (Join-Path $CheckoutRoot "zetherion-dev-agent")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install zetherion-dev-agent from source checkout"
}

Write-ConfigFile -Path $configPath -DatabasePath $databasePath -LogDir $logDir

$wrapper = @'
$env:ZETHERION_DEV_AGENT_HOME = "__CONFIG_HOME__"
$env:ZETHERION_EXECUTION_BACKEND = "__EXECUTION_BACKEND__"
$env:ZETHERION_DOCKER_BACKEND = "__DOCKER_BACKEND__"
$env:ZETHERION_WSL_DISTRIBUTION = "__WSL_DISTRIBUTION__"
$mutexName = "Global\ZetherionOwnerCiWorker"
$mutex = $null
$hasHandle = $false
try {
    $mutex = New-Object System.Threading.Mutex($false, $mutexName)
    try {
        $hasHandle = $mutex.WaitOne(0, $false)
    } catch [System.Threading.AbandonedMutexException] {
        $hasHandle = $true
    }

    if (-not $hasHandle) {
        Write-Host "Owner-CI worker already running; exiting duplicate launcher."
        exit 0
    }

    & "__PYTHON_EXE__" -m zetherion_dev_agent.cli worker
    exit $LASTEXITCODE
}
finally {
    if ($mutex -and $hasHandle) {
        $mutex.ReleaseMutex() | Out-Null
    }
    if ($mutex) {
        $mutex.Dispose()
    }
}
'@
$wrapper = $wrapper.Replace("__CONFIG_HOME__", $ConfigHome)
$wrapper = $wrapper.Replace("__EXECUTION_BACKEND__", $ExecutionBackend)
$wrapper = $wrapper.Replace("__DOCKER_BACKEND__", $DockerBackend)
$wrapper = $wrapper.Replace("__WSL_DISTRIBUTION__", $WslDistribution)
$wrapper = $wrapper.Replace("__PYTHON_EXE__", $pythonExe)
Set-Content -LiteralPath $wrapperPath -Value $wrapper

Register-WorkerTask -WrapperPath $wrapperPath

$wslKeepaliveWrapper = @'
$env:ZETHERION_WSL_DISTRIBUTION = "__WSL_DISTRIBUTION__"
& "__KEEPALIVE_SCRIPT__" -WslDistribution "__WSL_DISTRIBUTION__"
exit $LASTEXITCODE
'@
$wslKeepaliveWrapper = $wslKeepaliveWrapper.Replace("__WSL_DISTRIBUTION__", $WslDistribution)
$wslKeepaliveWrapper = $wslKeepaliveWrapper.Replace("__KEEPALIVE_SCRIPT__", $wslKeepaliveScriptPath)
Set-Content -LiteralPath $wslKeepaliveWrapperPath -Value $wslKeepaliveWrapper

Register-WslKeepaliveTask -WrapperPath $wslKeepaliveWrapperPath
Start-ScheduledTask -TaskName $WslKeepaliveTaskName

$wslDockerConfig = $null
if ($ExecutionBackend -eq "wsl_docker" -or $DockerBackend -eq "wsl_docker") {
    $wslDockerConfig = Ensure-ZetherionWslDockerHeadlessConfig
    if (-not [bool]$wslDockerConfig.reachable) {
        throw "Unable to prepare WSL Docker config for headless execution: $($wslDockerConfig.error)"
    }
    if (-not [bool]$wslDockerConfig.headless_ready) {
        throw "WSL Docker config still depends on a desktop credential helper."
    }
}

$env:ZETHERION_DEV_AGENT_HOME = $ConfigHome
$statusOutput = & $cliExe status
if ($LASTEXITCODE -ne 0) {
    throw "Installed CLI failed status check"
}

$receipt = [ordered]@{
    installed = $true
    repository_url = $RepositoryUrl
    branch = $Branch
    checkout_root = $CheckoutRoot
    workspace_root = $WorkspaceRoot
    runtime_root = $RuntimeRoot
    config_home = $ConfigHome
    config_path = $configPath
    venv_path = $venvPath
    cli_path = $cliExe
    wrapper_path = $wrapperPath
    task_name = $TaskName
    wsl_keepalive_wrapper_path = $wslKeepaliveWrapperPath
    wsl_keepalive_task_name = $WslKeepaliveTaskName
    scope_id = $ScopeId
    worker_base_url = $WorkerBaseUrl
    relay_base_url = $RelayBaseUrl
    execution_backend = $ExecutionBackend
    docker_backend = $DockerBackend
    wsl_distribution = $WslDistribution
    wsl_host_config = if ($wslHostConfig) {
        [ordered]@{
            path = [string]$wslHostConfig.path
            exists = [bool]$wslHostConfig.exists
            vm_idle_timeout_ms = $wslHostConfig.vm_idle_timeout_ms
            recommended_vm_idle_timeout_ms = [int64]$RecommendedWslVmIdleTimeoutMs
            changed = [bool]$wslConfigChanged
            restart_required = [bool]$wslConfigChanged
        }
    }
    else {
        $null
    }
    wsl_docker_config = if ($wslDockerConfig) {
        [ordered]@{
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
    worker_allowed_repo_roots = $AllowedRepoRoots
    denied_repo_roots = $DeniedRepoRoots
    status_output = @($statusOutput)
    installed_at = (Get-Date).ToUniversalTime().ToString("o")
}

$receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputPath
Write-Host "Owner-CI worker installed."
Write-Host "  Config: $configPath"
Write-Host "  Task:   $TaskName"
Write-Host "  Receipt: $OutputPath"
