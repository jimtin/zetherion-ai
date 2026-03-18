param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$CiRoot = "C:\ZetherionCI",
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu",
    [Parameter(Mandatory = $false)]
    [string[]]$TaskNames = @(
        "ZetherionStartupRecover",
        "ZetherionRuntimeWatchdog",
        "ZetherionPostDeployPromotions",
        "ZetherionDiscordCanary",
        "ZetherionWslKeepalive",
        "ZetherionOwnerCiWorker"
    ),
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionCI\artifacts\windows-forensic-receipt.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Ensure-ParentDir {
    param([string]$Path)

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
}

function Invoke-OptionalCommand {
    param(
        [scriptblock]$Script,
        [object]$Default
    )

    try {
        return & $Script
    }
    catch {
        return $Default
    }
}

function Get-GitForensics {
    param([string]$RepositoryPath)

    if (-not (Test-Path -LiteralPath $RepositoryPath)) {
        return [ordered]@{
            exists = $false
            head_sha = ""
            status = @()
            diff_stat = @()
        }
    }

    Push-Location $RepositoryPath
    try {
        $gitArgs = @("-c", "core.safecrlf=false")
        $headSha = ((git @gitArgs rev-parse HEAD 2>$null) | Out-String).Trim()
        $statusLines = @((git @gitArgs status --short 2>$null) | ForEach-Object { [string]$_ })
        $diffStat = @((git @gitArgs diff --stat 2>$null) | ForEach-Object { [string]$_ })
    }
    finally {
        Pop-Location
    }

    return [ordered]@{
        exists = $true
        head_sha = $headSha
        status = $statusLines
        diff_stat = $diffStat
    }
}

function Get-DirectoryListing {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    return @(
        Get-ChildItem -LiteralPath $Path -ErrorAction SilentlyContinue |
            Select-Object -First 40 -Property Name, FullName, PSIsContainer
    )
}

function Get-ScheduledTaskFacts {
    param([string[]]$Names)

    $facts = @()
    foreach ($name in $Names) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) {
            $facts += [ordered]@{
                task_name = $name
                exists = $false
            }
            continue
        }

        $facts += [ordered]@{
            task_name = $name
            exists = $true
            state = $task.State.ToString()
            principal_user = [string]$task.Principal.UserId
            actions = @(
                @($task.Actions) | ForEach-Object {
                    [ordered]@{
                        execute = [string]$_.Execute
                        arguments = [string]$_.Arguments
                    }
                }
            )
        }
    }

    return $facts
}

function Get-WslFacts {
    $listOutput = & wsl.exe -l -v 2>&1
    $activeProbe = Invoke-ZetherionWslCommandResult -Command "uname -a && systemctl is-active docker"

    return [ordered]@{
        distribution = $WslDistribution
        list_output = @($listOutput)
        probe_exit_code = [int]$activeProbe.ExitCode
        probe_output = @($activeProbe.Output)
    }
}

function Get-WslHostConfigFacts {
    $config = Get-ZetherionWslHostConfig
    return [ordered]@{
        path = [string]$config.path
        exists = [bool]$config.exists
        vm_idle_timeout_ms = $config.vm_idle_timeout_ms
        recommended_vm_idle_timeout_ms = $config.recommended_vm_idle_timeout_ms
        passes_idle_timeout = [bool]$config.passes_idle_timeout
        raw_lines = @($config.raw_lines)
    }
}

function Get-DockerFacts {
    param([string]$RepositoryPath)

    $runtimeStatus = Get-ZetherionDockerRuntimeStatus -ExecutionBackend "wsl_docker" -DockerBackend "wsl_docker"
    $desktopStatus = Get-ZetherionDockerDesktopStatus
    $contexts = Invoke-OptionalCommand -Default @() -Script {
        @(
            docker context ls --format "{{json .}}" 2>$null |
                ForEach-Object { [string]$_ }
        )
    }

    if (Test-Path -LiteralPath $RepositoryPath) {
        Push-Location $RepositoryPath
        try {
            $composePs = Invoke-OptionalCommand -Default @() -Script {
                @(
                    docker compose ps 2>&1 | ForEach-Object { [string]$_ }
                )
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        $composePs = @()
    }

    $dockerPs = Invoke-OptionalCommand -Default @() -Script {
        @(
            docker ps --format "{{.Names}}|{{.Status}}|{{.Image}}" 2>&1 |
                ForEach-Object { [string]$_ }
        )
    }

    return [ordered]@{
        runtime_status = [ordered]@{
            backend = [string]$runtimeStatus.backend
            distribution = [string]$runtimeStatus.distribution
            enabled = [bool]$runtimeStatus.enabled
            active = [bool]$runtimeStatus.active
            available = [bool]$runtimeStatus.available
        }
        desktop_status = $desktopStatus
        contexts = $contexts
        compose_ps = $composePs
        docker_ps = $dockerPs
    }
}

$receipt = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    execution_backend = "wsl_docker"
    docker_backend = "wsl_docker"
    wsl_distribution = $WslDistribution
    deploy_path = $DeployPath
    ci_root = $CiRoot
    deploy_repo = Get-GitForensics -RepositoryPath $DeployPath
    ci_root_listing = Get-DirectoryListing -Path $CiRoot
    wsl = Get-WslFacts
    wsl_host_config = Get-WslHostConfigFacts
    docker = Get-DockerFacts -RepositoryPath $DeployPath
    scheduled_tasks = Get-ScheduledTaskFacts -Names $TaskNames
}

Ensure-ParentDir -Path $OutputPath
$receipt | ConvertTo-Json -Depth 8 | Out-File -FilePath $OutputPath -Encoding utf8
Write-Host "Forensic receipt written to $OutputPath"
