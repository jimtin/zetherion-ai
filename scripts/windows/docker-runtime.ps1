Set-StrictMode -Version Latest

$script:ZetherionWslDistribution = if ($env:ZETHERION_WSL_DISTRIBUTION) {
    [string]$env:ZETHERION_WSL_DISTRIBUTION
} else {
    "Ubuntu"
}
$script:ZetherionExecutionBackend = if ($env:ZETHERION_EXECUTION_BACKEND) {
    [string]$env:ZETHERION_EXECUTION_BACKEND
} else {
    "wsl_docker"
}
$script:ZetherionDockerBackend = if ($env:ZETHERION_DOCKER_BACKEND) {
    [string]$env:ZETHERION_DOCKER_BACKEND
} else {
    $script:ZetherionExecutionBackend
}
$script:ZetherionRecommendedWslVmIdleTimeoutMs = 604800000

function Get-ZetherionWslDistribution {
    return $script:ZetherionWslDistribution
}

function Get-ZetherionExecutionBackend {
    return $script:ZetherionExecutionBackend
}

function Get-ZetherionDockerBackend {
    return $script:ZetherionDockerBackend
}

function Get-ZetherionRecommendedWslVmIdleTimeoutMs {
    return [int64]$script:ZetherionRecommendedWslVmIdleTimeoutMs
}

function Get-ZetherionWslHostConfigPath {
    return Join-Path $env:USERPROFILE ".wslconfig"
}

function Get-ZetherionWslHostConfig {
    $path = Get-ZetherionWslHostConfigPath
    $exists = Test-Path -LiteralPath $path
    $rawLines = if ($exists) {
        @(
            Get-Content -LiteralPath $path -ErrorAction SilentlyContinue |
                ForEach-Object { [string]$_ }
        )
    } else {
        @()
    }

    $section = ""
    $vmIdleTimeoutMs = $null
    foreach ($rawLine in $rawLines) {
        $trimmed = ([string]$rawLine).Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or $trimmed.StartsWith(";")) {
            continue
        }
        if ($trimmed -match "^\[(.+)\]$") {
            $section = $Matches[1].Trim().ToLowerInvariant()
            continue
        }
        if ($section -ne "wsl2") {
            continue
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }
        $key = $parts[0].Trim().ToLowerInvariant()
        $value = $parts[1].Trim()
        if ($key -eq "vmidletimeout") {
            $parsedValue = [int64]0
            if ([int64]::TryParse($value, [ref]$parsedValue)) {
                $vmIdleTimeoutMs = $parsedValue
            }
        }
    }

    $recommended = Get-ZetherionRecommendedWslVmIdleTimeoutMs
    return [pscustomobject]@{
        path = $path
        exists = [bool]$exists
        raw_lines = @($rawLines)
        vm_idle_timeout_ms = if ($null -ne $vmIdleTimeoutMs) { [int64]$vmIdleTimeoutMs } else { $null }
        recommended_vm_idle_timeout_ms = [int64]$recommended
        passes_idle_timeout = [bool]($null -ne $vmIdleTimeoutMs -and [int64]$vmIdleTimeoutMs -ge [int64]$recommended)
    }
}

function Set-ZetherionWslHostVmIdleTimeout {
    param(
        [int64]$VmIdleTimeoutMs = (Get-ZetherionRecommendedWslVmIdleTimeoutMs)
    )

    $path = Get-ZetherionWslHostConfigPath
    $existingLines = if (Test-Path -LiteralPath $path) {
        @(
            Get-Content -LiteralPath $path -ErrorAction SilentlyContinue |
                ForEach-Object { [string]$_ }
        )
    } else {
        @()
    }

    $newLines = New-Object 'System.Collections.Generic.List[string]'
    $inWsl2Section = $false
    $sawWsl2Section = $false
    $wroteVmIdleTimeout = $false

    foreach ($rawLine in $existingLines) {
        $line = [string]$rawLine
        $trimmed = $line.Trim()

        if ($trimmed -match "^\[(.+)\]$") {
            if ($inWsl2Section -and -not $wroteVmIdleTimeout) {
                $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
                $wroteVmIdleTimeout = $true
            }
            $section = $Matches[1].Trim().ToLowerInvariant()
            $inWsl2Section = ($section -eq "wsl2")
            if ($inWsl2Section) {
                $sawWsl2Section = $true
            }
            $newLines.Add($line)
            continue
        }

        if ($inWsl2Section -and $trimmed -match "^vmIdleTimeout\s*=") {
            if (-not $wroteVmIdleTimeout) {
                $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
                $wroteVmIdleTimeout = $true
            }
            continue
        }

        $newLines.Add($line)
    }

    if (-not $sawWsl2Section) {
        if ($newLines.Count -gt 0 -and $newLines[$newLines.Count - 1].Trim() -ne "") {
            $newLines.Add("")
        }
        $newLines.Add("[wsl2]")
        $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
    }
    elseif ($sawWsl2Section -and -not $wroteVmIdleTimeout) {
        $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
    }

    $parent = Split-Path -Parent $path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Set-Content -LiteralPath $path -Value $newLines -Encoding utf8
    return Get-ZetherionWslHostConfig
}

function Get-ZetherionWslDockerConfigStatus {
    param(
        [switch]$Repair
    )

    $repairLiteral = if ($Repair) { "True" } else { "False" }
    $pythonScript = @'
import json
from pathlib import Path

repair = __REPAIR__
path = Path.home() / ".docker" / "config.json"
result = {
    "path": str(path),
    "exists": path.exists(),
    "reachable": True,
    "changed": False,
    "repaired": repair,
    "valid_json": True,
    "parse_error": "",
    "backup_path": "",
    "creds_store": "",
    "removed_desktop_helper": False,
    "removed_cred_helpers": [],
    "headless_ready": True,
}

data = {}
if path.exists():
    raw_text = path.read_text(encoding="utf-8")
    if raw_text.strip():
        try:
            parsed = json.loads(raw_text)
        except Exception as exc:
            result["valid_json"] = False
            result["parse_error"] = str(exc)
            if not repair:
                result["headless_ready"] = False
                print(json.dumps(result))
                raise SystemExit(0)
            backup_path = path.with_suffix(path.suffix + ".broken")
            backup_path.write_text(raw_text, encoding="utf-8")
            result["backup_path"] = str(backup_path)
            data = {}
            result["changed"] = True
        else:
            if isinstance(parsed, dict):
                data = dict(parsed)
            else:
                result["valid_json"] = False
                result["parse_error"] = "config.json must contain a JSON object"
                if not repair:
                    result["headless_ready"] = False
                    print(json.dumps(result))
                    raise SystemExit(0)
                backup_path = path.with_suffix(path.suffix + ".broken")
                backup_path.write_text(raw_text, encoding="utf-8")
                result["backup_path"] = str(backup_path)
                data = {}
                result["changed"] = True

creds_store = data.get("credsStore")
legacy_cred_store = data.get("credStore")
active_store = creds_store if isinstance(creds_store, str) else legacy_cred_store
if isinstance(active_store, str):
    result["creds_store"] = active_store

if isinstance(creds_store, str) and "desktop" in creds_store.lower():
    data.pop("credsStore", None)
    result["removed_desktop_helper"] = True
    result["changed"] = True
if isinstance(legacy_cred_store, str) and "desktop" in legacy_cred_store.lower():
    data.pop("credStore", None)
    result["removed_desktop_helper"] = True
    result["changed"] = True

cred_helpers = data.get("credHelpers")
if isinstance(cred_helpers, dict):
    kept_helpers = {}
    removed_helpers = []
    for registry, helper in cred_helpers.items():
        helper_text = str(helper)
        if "desktop" in helper_text.lower():
            removed_helpers.append(str(registry))
            result["changed"] = True
        else:
            kept_helpers[str(registry)] = helper
    if removed_helpers:
        result["removed_cred_helpers"] = removed_helpers
        if kept_helpers:
            data["credHelpers"] = kept_helpers
        else:
            data.pop("credHelpers", None)

desktop_remaining = False
if isinstance(data.get("credsStore"), str) and "desktop" in str(data.get("credsStore")).lower():
    desktop_remaining = True
if isinstance(data.get("credStore"), str) and "desktop" in str(data.get("credStore")).lower():
    desktop_remaining = True
if isinstance(data.get("credHelpers"), dict):
    desktop_remaining = desktop_remaining or any(
        "desktop" in str(helper).lower() for helper in data["credHelpers"].values()
    )

result["headless_ready"] = not desktop_remaining

if repair and result["changed"]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["exists"] = True

print(json.dumps(result))
'@
    $pythonScript = $pythonScript.Replace("__REPAIR__", $repairLiteral)
    $tempScriptPath = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("zetherion-wsl-docker-config-" + [guid]::NewGuid().ToString("N") + ".py")
    Set-Content -LiteralPath $tempScriptPath -Value $pythonScript -Encoding utf8
    $tempScriptWslPath = ConvertTo-ZetherionWslPath -WindowsPath $tempScriptPath

    try {
        $command = "python3 " + (ConvertTo-ZetherionBashLiteral -Value $tempScriptWslPath)
        $probe = Invoke-ZetherionWslCommandResult -Command $command
    }
    finally {
        Remove-Item -LiteralPath $tempScriptPath -Force -ErrorAction SilentlyContinue
    }

    if ($probe.ExitCode -ne 0) {
        return [pscustomobject]@{
            path = "~/.docker/config.json"
            exists = $false
            reachable = $false
            changed = $false
            repaired = [bool]$Repair
            valid_json = $false
            parse_error = ""
            backup_path = ""
            creds_store = ""
            removed_desktop_helper = $false
            removed_cred_helpers = @()
            headless_ready = $false
            error = $probe.Text
        }
    }

    try {
        $payload = $probe.Text | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return [pscustomobject]@{
            path = "~/.docker/config.json"
            exists = $false
            reachable = $false
            changed = $false
            repaired = [bool]$Repair
            valid_json = $false
            parse_error = ""
            backup_path = ""
            creds_store = ""
            removed_desktop_helper = $false
            removed_cred_helpers = @()
            headless_ready = $false
            error = $_.Exception.Message
        }
    }

    return $payload
}

function Ensure-ZetherionWslDockerHeadlessConfig {
    return Get-ZetherionWslDockerConfigStatus -Repair
}

function ConvertTo-ZetherionWslPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WindowsPath
    )

    if ($WindowsPath -match "^[A-Za-z]:\\") {
        $drive = $WindowsPath.Substring(0, 1).ToLowerInvariant()
        $rest = $WindowsPath.Substring(2).Replace("\", "/")
        return "/mnt/$drive$rest"
    }

    return $WindowsPath.Replace("\", "/")
}

function ConvertTo-ZetherionBashLiteral {
    param(
        [AllowNull()]
        [string]$Value
    )

    if ($null -eq $Value) {
        return "''"
    }

    return "'" + $Value.Replace("'", "'""'""'") + "'"
}

function Invoke-ZetherionWslCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [string]$User = ""
    )

    $result = Invoke-ZetherionWslCommandResult -Command $Command -User $User

    foreach ($entry in @($result.Output)) {
        Write-Output $entry
    }

    if ($result.ExitCode -ne 0) {
        $message = $result.Text
        if (-not $message) {
            $message = "WSL command failed with exit code $($result.ExitCode)."
        }
        throw $message
    }
}

function Invoke-ZetherionWslCommandResult {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [string]$User = ""
    )

    $distro = Get-ZetherionWslDistribution
    $wslArgs = New-Object 'System.Collections.Generic.List[string]'
    $wslArgs.Add("-d")
    $wslArgs.Add($distro)
    if ($User) {
        $wslArgs.Add("-u")
        $wslArgs.Add($User)
    }
    $wslArgs.Add("--")
    $wslArgs.Add("bash")
    $wslArgs.Add("-lc")
    $wslArgs.Add($Command)

    $output = & wsl.exe @wslArgs 2>&1
    $exitCode = $LASTEXITCODE
    $global:LASTEXITCODE = $exitCode

    return [pscustomobject]@{
        Output = @($output)
        ExitCode = $exitCode
        Text = ($output | Out-String).Trim()
    }
}

function Get-ZetherionNativeDockerRuntimeStatus {
    $dockerCli = Get-Command "docker.exe" -ErrorAction SilentlyContinue
    $cliAvailable = $null -ne $dockerCli

    if ($cliAvailable) {
        & $dockerCli.Source info *> $null
        $available = ($LASTEXITCODE -eq 0)
        $distributionOutput = (& $dockerCli.Source version --format "{{.Server.Os}}/{{.Server.Version}}" 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            $distributionOutput = ""
        }
    }
    else {
        $available = $false
        $distributionOutput = ""
    }

    return [pscustomobject]@{
        backend = "native_windows_docker"
        distribution = if ($distributionOutput) { $distributionOutput } else { "native_windows" }
        enabled = [bool]$cliAvailable
        active = [bool]$available
        available = [bool]$available
    }
}

function Get-ZetherionDockerRuntimeStatus {
    param(
        [string]$ExecutionBackend = "",
        [string]$DockerBackend = ""
    )

    $resolvedExecutionBackend = if ($ExecutionBackend) {
        $ExecutionBackend
    } else {
        Get-ZetherionExecutionBackend
    }
    $resolvedDockerBackend = if ($DockerBackend) {
        $DockerBackend
    } else {
        Get-ZetherionDockerBackend
    }

    if ($resolvedExecutionBackend -eq "native_windows_docker" -or $resolvedDockerBackend -eq "native_windows_docker") {
        return Get-ZetherionNativeDockerRuntimeStatus
    }

    $distro = Get-ZetherionWslDistribution

    $enabledOutput = (& wsl.exe -d $distro -- bash -lc "systemctl is-enabled docker 2>/dev/null || true" 2>&1 | Out-String).Trim()
    $enabled = ($LASTEXITCODE -eq 0 -or $enabledOutput) -and ($enabledOutput -eq "enabled")

    $activeOutput = (& wsl.exe -d $distro -- bash -lc "systemctl is-active docker 2>/dev/null || true" 2>&1 | Out-String).Trim()
    $active = ($LASTEXITCODE -eq 0 -or $activeOutput) -and ($activeOutput -eq "active")

    & wsl.exe -d $distro -- bash -lc "docker info >/dev/null 2>&1"
    $available = ($LASTEXITCODE -eq 0)

    return [pscustomobject]@{
        backend = "wsl_docker"
        distribution = $distro
        enabled = [bool]$enabled
        active = [bool]$active
        available = [bool]$available
    }
}

function Test-ZetherionDockerAvailable {
    $status = Get-ZetherionDockerRuntimeStatus
    return [bool]($status.enabled -and $status.active -and $status.available)
}

function Get-ZetherionDiskStatus {
    param(
        [string]$Path = "C:\",
        [int64]$LowDiskFreeBytes = 21474836480,
        [int64]$TargetFreeBytes = 42949672960
    )

    $resolvedPath = $Path
    if (-not (Test-Path -LiteralPath $resolvedPath)) {
        $resolvedPath = [System.IO.Path]::GetPathRoot($Path)
    }
    $item = Get-Item -LiteralPath $resolvedPath -ErrorAction Stop
    $drive = $item.PSDrive
    $freeBytes = [int64]$drive.Free
    $usedBytes = [int64]$drive.Used
    $totalBytes = [int64]($usedBytes + $freeBytes)

    return [pscustomobject]@{
        path = $resolvedPath
        root = [string]$drive.Root
        name = [string]$drive.Name
        used_bytes = $usedBytes
        free_bytes = $freeBytes
        total_bytes = $totalBytes
        low_disk_free_bytes = [int64]$LowDiskFreeBytes
        target_free_bytes = [int64]$TargetFreeBytes
        under_pressure = [bool]($freeBytes -lt $LowDiskFreeBytes)
        target_met = [bool]($freeBytes -ge $TargetFreeBytes)
    }
}

function Remove-ZetherionCleanupItem {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Actions,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Warnings,
        [string]$ActionType = "remove_path"
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        $Actions.Add([ordered]@{
            action = $ActionType
            target = $Path
            success = $true
        }) | Out-Null
    }
    catch {
        $Warnings.Add("${ActionType}:${Path}:$($_.Exception.Message)") | Out-Null
        $Actions.Add([ordered]@{
            action = $ActionType
            target = $Path
            success = $false
            error = $_.Exception.Message
        }) | Out-Null
    }
}

function Get-ZetherionTrackedComposeProjectManifests {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Warnings
    )

    $tracked = @{}
    if (-not (Test-Path -LiteralPath $WorkspaceRoot)) {
        return $tracked
    }

    $manifestRoots = @(
        (Join-Path $WorkspaceRoot ".artifacts\ci-e2e-runs\manifests"),
        (Join-Path $WorkspaceRoot ".artifacts\e2e-runs\manifests")
    )
    foreach ($manifestRoot in $manifestRoots) {
        if (-not (Test-Path -LiteralPath $manifestRoot)) {
            continue
        }
        foreach ($manifestPath in @(Get-ChildItem -LiteralPath $manifestRoot -Filter "*.json" -File -ErrorAction SilentlyContinue)) {
            try {
                $payload = Get-Content -LiteralPath $manifestPath.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            }
            catch {
                $Warnings.Add("compose_manifest_parse_failed:$($manifestPath.FullName):$($_.Exception.Message)") | Out-Null
                continue
            }

            $projectName = [string]$payload.compose_project
            if ([string]::IsNullOrWhiteSpace($projectName)) {
                continue
            }

            $expiresAt = $null
            $lease = $payload.lease
            if ($lease -and $lease.expires_at) {
                try {
                    $expiresAt = [datetimeoffset]::Parse([string]$lease.expires_at).UtcDateTime
                }
                catch {
                    $expiresAt = $null
                }
            }

            $candidate = [ordered]@{
                project = $projectName
                manifest_path = $manifestPath.FullName
                last_write_utc = $manifestPath.LastWriteTimeUtc
                cleanup_status = [string]$payload.cleanup.status
                lease_status = [string]$payload.lease.status
                expires_at_utc = $expiresAt
            }

            $existing = $tracked[$projectName]
            if ($null -eq $existing -or [datetime]$candidate.last_write_utc -gt [datetime]$existing.last_write_utc) {
                $tracked[$projectName] = $candidate
            }
        }
    }

    return $tracked
}

function Get-ZetherionComposeProjects {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Warnings
    )

    $result = Invoke-ZetherionDockerResult compose ls --format json
    if ($result.ExitCode -ne 0) {
        $Warnings.Add("compose_ls_failed:$($result.Text)") | Out-Null
        return @()
    }

    $rawJson = [string]$result.Text
    if ([string]::IsNullOrWhiteSpace($rawJson)) {
        return @()
    }

    try {
        $payload = $rawJson | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        $Warnings.Add("compose_ls_parse_failed:$($_.Exception.Message)") | Out-Null
        return @()
    }

    $projects = New-Object 'System.Collections.Generic.List[object]'
    foreach ($entry in @($payload)) {
        if (-not $entry) {
            continue
        }
        $name = ""
        if ($entry.PSObject.Properties.Name -contains "Name") {
            $name = [string]$entry.Name
        }
        elseif ($entry.PSObject.Properties.Name -contains "Project") {
            $name = [string]$entry.Project
        }
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }
        $projects.Add([ordered]@{
            name = $name
            status = if ($entry.PSObject.Properties.Name -contains "Status") { [string]$entry.Status } else { "" }
            source = "compose_ls"
        }) | Out-Null
    }
    return @($projects.ToArray())
}

function Get-ZetherionComposeProjectCreatedAtUtc {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectName,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Warnings
    )

    $idsResult = Invoke-ZetherionDockerResult ps -aq --filter "label=com.docker.compose.project=$ProjectName"
    if ($idsResult.ExitCode -ne 0) {
        $Warnings.Add("compose_project_ids_failed:${ProjectName}:$($idsResult.Text)") | Out-Null
        return $null
    }

    $containerIds = @($idsResult.Output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($containerIds.Count -eq 0) {
        return $null
    }

    $inspectResult = Invoke-ZetherionDockerResult inspect --format "{{.Created}}" $containerIds[0]
    if ($inspectResult.ExitCode -ne 0) {
        $Warnings.Add("compose_project_created_failed:${ProjectName}:$($inspectResult.Text)") | Out-Null
        return $null
    }

    try {
        return [datetimeoffset]::Parse([string]$inspectResult.Text).UtcDateTime
    }
    catch {
        $Warnings.Add("compose_project_created_parse_failed:${ProjectName}:$($_.Exception.Message)") | Out-Null
        return $null
    }
}

function Remove-ZetherionDockerResourcesByLabel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LabelFilter,
        [Parameter(Mandatory = $true)]
        [string]$ProjectName,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Actions,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Warnings
    )

    $resourceSpecs = @(
        @{ suffix = "containers"; list_args = @("ps", "-aq"); remove_args = @("rm", "-f") },
        @{ suffix = "networks"; list_args = @("network", "ls", "-q"); remove_args = @("network", "rm") },
        @{ suffix = "volumes"; list_args = @("volume", "ls", "-q"); remove_args = @("volume", "rm", "-f") }
    )

    foreach ($resource in $resourceSpecs) {
        $queryArgs = @($resource.list_args) + @("--filter", $LabelFilter)
        $queryResult = Invoke-ZetherionDockerResult @queryArgs
        $Actions.Add([ordered]@{
            action = "stale_compose_project_$($resource.suffix)_query"
            target = $ProjectName
            label_filter = $LabelFilter
            success = [bool]($queryResult.ExitCode -eq 0)
            exit_code = [int]$queryResult.ExitCode
            output = [string]$queryResult.Text
        }) | Out-Null
        if ($queryResult.ExitCode -ne 0) {
            $Warnings.Add("stale_compose_project_query_failed:${ProjectName}:$($resource.suffix):$($queryResult.Text)") | Out-Null
            continue
        }

        $resourceIds = @($queryResult.Output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
        if ($resourceIds.Count -eq 0) {
            continue
        }

        $removeArgs = @($resource.remove_args) + @($resourceIds)
        $removeResult = Invoke-ZetherionDockerResult @removeArgs
        $Actions.Add([ordered]@{
            action = "stale_compose_project_$($resource.suffix)_remove"
            target = $ProjectName
            label_filter = $LabelFilter
            resources = @($resourceIds)
            success = [bool]($removeResult.ExitCode -eq 0)
            exit_code = [int]$removeResult.ExitCode
            output = [string]$removeResult.Text
        }) | Out-Null
        if ($removeResult.ExitCode -ne 0) {
            $Warnings.Add("stale_compose_project_remove_failed:${ProjectName}:$($resource.suffix):$($removeResult.Text)") | Out-Null
        }
    }
}

function Remove-ZetherionStaleComposeProjects {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Actions,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Warnings,
        [int]$MaxAgeMinutes = 90
    )

    $trackedProjects = Get-ZetherionTrackedComposeProjectManifests -WorkspaceRoot $WorkspaceRoot -Warnings $Warnings
    $composeProjects = Get-ZetherionComposeProjects -Warnings $Warnings
    if (@($composeProjects).Count -eq 0) {
        return
    }

    $nowUtc = (Get-Date).ToUniversalTime()
    $staleCutoffUtc = $nowUtc.AddMinutes(-1 * [Math]::Max(15, $MaxAgeMinutes))
    foreach ($project in @($composeProjects)) {
        $projectName = [string]$project.name
        if ([string]::IsNullOrWhiteSpace($projectName)) {
            continue
        }
        if ($projectName -notlike "zetherion-ai-test-run-*" -and $projectName -notlike "owner-ci-*") {
            continue
        }

        $tracked = $trackedProjects[$projectName]
        $cleanupReason = ""
        if ($tracked) {
            $cleanupStatus = [string]$tracked.cleanup_status
            $leaseStatus = [string]$tracked.lease_status
            if ($cleanupStatus -in @("cleaned", "cleanup_failed")) {
                $cleanupReason = "manifest_cleanup_status:$cleanupStatus"
            }
            elseif ($leaseStatus -in @("cleaned", "cleanup_failed")) {
                $cleanupReason = "manifest_lease_status:$leaseStatus"
            }
            elseif ($tracked.expires_at_utc -and [datetime]$tracked.expires_at_utc -le $nowUtc) {
                $cleanupReason = "manifest_expired"
            }
            elseif ([datetime]$tracked.last_write_utc -le $staleCutoffUtc) {
                $cleanupReason = "manifest_abandoned"
            }
        }
        else {
            $createdAtUtc = Get-ZetherionComposeProjectCreatedAtUtc -ProjectName $projectName -Warnings $Warnings
            if ($createdAtUtc -and [datetime]$createdAtUtc -le $staleCutoffUtc) {
                $cleanupReason = "project_older_than_threshold"
            }
        }

        if ([string]::IsNullOrWhiteSpace($cleanupReason)) {
            continue
        }

        $Actions.Add([ordered]@{
            action = "stale_compose_project_detected"
            target = $projectName
            success = $true
            reason = $cleanupReason
        }) | Out-Null
        Remove-ZetherionDockerResourcesByLabel `
            -LabelFilter "label=com.docker.compose.project=$projectName" `
            -ProjectName $projectName `
            -Actions $Actions `
            -Warnings $Warnings
    }
}

function Invoke-ZetherionDiskCleanup {
    param(
        [string]$CiRoot = "C:\ZetherionCI",
        [int64]$LowDiskFreeBytes = 21474836480,
        [int64]$TargetFreeBytes = 42949672960,
        [int]$ArtifactRetentionHours = 24,
        [int]$LogRetentionDays = 7,
        [switch]$Aggressive
    )

    $before = Get-ZetherionDiskStatus -Path $CiRoot -LowDiskFreeBytes $LowDiskFreeBytes -TargetFreeBytes $TargetFreeBytes
    $actions = New-Object 'System.Collections.Generic.List[object]'
    $warnings = New-Object 'System.Collections.Generic.List[string]'
    $workspaceRoot = Join-Path $CiRoot "workspaces"
    $artifactsRoot = Join-Path $CiRoot "artifacts"
    $logsRoot = Join-Path $CiRoot "logs"
    $runtimeRoot = Join-Path $CiRoot "agent-runtime"
    $artifactCutoff = (Get-Date).ToUniversalTime().AddHours(-1 * [Math]::Max(1, $ArtifactRetentionHours))
    $logCutoff = (Get-Date).ToUniversalTime().AddDays(-1 * [Math]::Max(1, $LogRetentionDays))
    $staleComposeProjectMinutes = if ($Aggressive -or $before.under_pressure) { 30 } else { 90 }
    $preservedArtifactFiles = @(
        "ci-worker-connectivity.json",
        "e2e-receipt.json",
        "local-readiness-receipt.json",
        "worker-certification-receipt.json",
        "workspace-readiness-receipt.json"
    )

    if (Test-Path -LiteralPath $workspaceRoot) {
        foreach ($workspace in @(Get-ChildItem -LiteralPath $workspaceRoot -Directory -ErrorAction SilentlyContinue)) {
            foreach ($name in @(
                "test-results",
                "playwright-report",
                "htmlcov",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".next",
                ".swc"
            )) {
                Remove-ZetherionCleanupItem `
                    -Path (Join-Path $workspace.FullName $name) `
                    -Actions $actions `
                    -Warnings $warnings `
                    -ActionType "workspace_artifact_cleanup"
            }

            foreach ($file in @(Get-ChildItem -LiteralPath $workspace.FullName -Force -File -ErrorAction SilentlyContinue)) {
                if ($file.Name -like ".coverage*" -or $file.Name -eq "coverage.xml" -or $file.Name -eq "pytestdebug.log") {
                    Remove-ZetherionCleanupItem `
                        -Path $file.FullName `
                        -Actions $actions `
                        -Warnings $warnings `
                        -ActionType "workspace_artifact_cleanup"
                }
            }

            $workspaceArtifacts = Join-Path $workspace.FullName ".artifacts"
            if (Test-Path -LiteralPath $workspaceArtifacts) {
                foreach ($child in @(Get-ChildItem -LiteralPath $workspaceArtifacts -Force -ErrorAction SilentlyContinue)) {
                    $preserve = ($preservedArtifactFiles -contains $child.Name)
                    $lastWriteUtc = $child.LastWriteTimeUtc
                    if ($preserve) {
                        continue
                    }
                    if ($child.PSIsContainer -or $lastWriteUtc -lt $artifactCutoff) {
                        Remove-ZetherionCleanupItem `
                            -Path $child.FullName `
                            -Actions $actions `
                            -Warnings $warnings `
                            -ActionType "workspace_artifact_cleanup"
                    }
                }
            }
        }
    }

    foreach ($root in @($artifactsRoot, $logsRoot)) {
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }
        foreach ($child in @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue)) {
            $preserve = ($preservedArtifactFiles -contains $child.Name)
            if ($preserve) {
                continue
            }
            $cutoff = if ($root -eq $logsRoot) { $logCutoff } else { $artifactCutoff }
            if ($child.PSIsContainer -or $child.LastWriteTimeUtc -lt $cutoff) {
                Remove-ZetherionCleanupItem `
                    -Path $child.FullName `
                    -Actions $actions `
                    -Warnings $warnings `
                    -ActionType "ci_root_cleanup"
            }
        }
    }

    if (Test-Path -LiteralPath $runtimeRoot) {
        foreach ($child in @(Get-ChildItem -LiteralPath $runtimeRoot -Force -ErrorAction SilentlyContinue)) {
            if ($child.Name -eq "home") {
                continue
            }
            if ($child.Name -eq "venv") {
                continue
            }
            if ($child.Name -like "*.ps1" -or $child.Name -like "*.cmd" -or $child.Name -like "*.sql" -or $child.Name -like "*.py") {
                continue
            }
            if ($child.LastWriteTimeUtc -lt $artifactCutoff) {
                Remove-ZetherionCleanupItem `
                    -Path $child.FullName `
                    -Actions $actions `
                    -Warnings $warnings `
                    -ActionType "runtime_artifact_cleanup"
            }
        }
    }

    Remove-ZetherionStaleComposeProjects `
        -WorkspaceRoot $workspaceRoot `
        -Actions $actions `
        -Warnings $warnings `
        -MaxAgeMinutes $staleComposeProjectMinutes

    $dockerCommands = New-Object 'System.Collections.Generic.List[object]'
    $dockerCommands.Add(@{ name = "container_prune"; args = @("container", "prune", "-f") }) | Out-Null
    $dockerCommands.Add(@{ name = "network_prune"; args = @("network", "prune", "-f") }) | Out-Null
    $dockerCommands.Add(@{ name = "image_prune_dangling"; args = @("image", "prune", "-f") }) | Out-Null
    $dockerCommands.Add(@{ name = "builder_prune_standard"; args = @("builder", "prune", "-f", "--filter", "until=24h") }) | Out-Null

    if ($Aggressive -or $before.under_pressure) {
        $dockerCommands.Add(@{ name = "image_prune_unused"; args = @("image", "prune", "-af", "--filter", "until=168h") }) | Out-Null
        $dockerCommands.Add(@{ name = "builder_prune_all"; args = @("builder", "prune", "-af") }) | Out-Null
        $dockerCommands.Add(@{ name = "volume_prune_unused"; args = @("volume", "prune", "-f") }) | Out-Null
    }

    foreach ($dockerCommand in $dockerCommands) {
        $dockerArgs = @($dockerCommand.args)
        $result = Invoke-ZetherionDockerResult @dockerArgs
        $actions.Add([ordered]@{
            action = [string]$dockerCommand.name
            target = "docker"
            success = [bool]($result.ExitCode -eq 0)
            exit_code = [int]$result.ExitCode
            output = [string]$result.Text
        }) | Out-Null
        if ($result.ExitCode -ne 0) {
            $warnings.Add("docker_cleanup:$($dockerCommand.name):$($result.Text)") | Out-Null
        }
    }

    $after = Get-ZetherionDiskStatus -Path $CiRoot -LowDiskFreeBytes $LowDiskFreeBytes -TargetFreeBytes $TargetFreeBytes
    $status = "cleaned"
    if ($warnings.Count -gt 0 -or $after.under_pressure) {
        $status = "cleanup_degraded"
    }

    return [pscustomobject]([ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        status = $status
        ci_root = $CiRoot
        before = $before
        after = $after
        aggressive = [bool]($Aggressive -or $before.under_pressure)
        artifact_retention_hours = [int]$ArtifactRetentionHours
        log_retention_days = [int]$LogRetentionDays
        stale_compose_project_minutes = [int]$staleComposeProjectMinutes
        actions = @($actions.ToArray())
        warnings = @($warnings.ToArray())
    })
}

function Ensure-ZetherionWslRuntimePaths {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeployPath,
        [string[]]$RelativePaths = @("data", "logs")
    )

    $repoWslPath = ConvertTo-ZetherionWslPath -WindowsPath $DeployPath
    $driveMatch = [regex]::Match($repoWslPath, "^(/mnt/[a-z])(?:/|$)")
    if (-not $driveMatch.Success) {
        throw "Deploy path '$DeployPath' is not on a supported WSL drvfs mount."
    }

    $driveRoot = $driveMatch.Groups[1].Value
    $escapedRelativePaths = New-Object 'System.Collections.Generic.List[string]'
    foreach ($relativePath in $RelativePaths) {
        $escapedRelativePaths.Add((ConvertTo-ZetherionBashLiteral -Value $relativePath))
    }

    $relativePathsLiteral = $escapedRelativePaths -join " "
    $command = @'
set -euo pipefail
repo_path=__REPO_PATH__
drive_root=__DRIVE_ROOT__
mount_line=\$(mount | grep " on \$drive_root " | head -n 1 || true)
if [ -z "\$mount_line" ]; then
  echo "Unable to resolve WSL mount metadata for \$drive_root." >&2
  exit 1
fi
case "\$mount_line" in
  *metadata*) ;;
  *)
    echo "WSL automount for \$drive_root must include the metadata option to support writable runtime bind mounts." >&2
    exit 1
    ;;
esac
owner_user=\$(stat -c '%U' "\$repo_path")
owner_group=\$(stat -c '%G' "\$repo_path")
if [ -z "\$owner_user" ] || [ "\$owner_user" = "UNKNOWN" ]; then
  echo "Unable to resolve a writable WSL owner for \$repo_path." >&2
  exit 1
fi
for rel in __RELATIVE_PATHS__; do
  target="\$repo_path/\$rel"
  mkdir -p "\$target"
  chown -R "\$owner_user:\$owner_group" "\$target"
  chmod -R u+rwX,g+rwX "\$target"
  runuser -u "\$owner_user" -- touch "\$target/.wsl-write-check"
  rm -f "\$target/.wsl-write-check"
done
'@
    $command = $command.Replace("__REPO_PATH__", (ConvertTo-ZetherionBashLiteral -Value $repoWslPath))
    $command = $command.Replace("__DRIVE_ROOT__", (ConvertTo-ZetherionBashLiteral -Value $driveRoot))
    $command = $command.Replace("__RELATIVE_PATHS__", $relativePathsLiteral)

    try {
        Invoke-ZetherionWslCommand -User "root" -Command $command | Out-Null
    } catch {
        $reason = $_.Exception.Message
        if ($reason) {
            throw "Failed to prepare WSL runtime paths for Docker bind mounts under '$DeployPath': $reason"
        }
        throw "Failed to prepare WSL runtime paths for Docker bind mounts under '$DeployPath'."
    }
}

function Invoke-ZetherionNativeDocker {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    $result = Invoke-ZetherionNativeDockerResult @DockerArguments
    foreach ($entry in @($result.Output)) {
        Write-Output $entry
    }

    if ($result.ExitCode -ne 0) {
        $message = $result.Text
        if (-not $message) {
            $message = "Docker command failed with exit code $($result.ExitCode)."
        }
        throw $message
    }
}

function Invoke-ZetherionNativeDockerResult {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    $dockerCli = Get-Command "docker.exe" -ErrorAction SilentlyContinue
    if (-not $dockerCli) {
        return [pscustomobject]@{
            Output = @("Unable to locate docker.exe on PATH.")
            ExitCode = 127
            Text = "Unable to locate docker.exe on PATH."
        }
    }

    $output = & $dockerCli.Source @DockerArguments 2>&1
    $exitCode = $LASTEXITCODE
    $global:LASTEXITCODE = $exitCode

    return [pscustomobject]@{
        Output = @($output)
        ExitCode = $exitCode
        Text = ($output | Out-String).Trim()
    }
}

function Invoke-ZetherionWslDocker {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    $result = Invoke-ZetherionWslDockerResult @DockerArguments
    foreach ($entry in @($result.Output)) {
        Write-Output $entry
    }

    if ($result.ExitCode -ne 0) {
        $message = $result.Text
        if (-not $message) {
            $message = "Docker command failed with exit code $($result.ExitCode)."
        }
        throw $message
    }
}

function Invoke-ZetherionWslDockerResult {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    $currentPath = (Get-Location).Path
    $wslPath = ConvertTo-ZetherionWslPath -WindowsPath $currentPath

    $escapedDockerArgs = New-Object 'System.Collections.Generic.List[string]'
    $escapedDockerArgs.Add("docker")
    foreach ($argument in $DockerArguments) {
        $escapedDockerArgs.Add((ConvertTo-ZetherionBashLiteral -Value ([string]$argument)))
    }

    $command = "cd $(ConvertTo-ZetherionBashLiteral -Value $wslPath) && $($escapedDockerArgs -join ' ')"
    return (Invoke-ZetherionWslCommandResult -Command $command)
}

function Invoke-ZetherionDocker {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    $result = Invoke-ZetherionDockerResult @DockerArguments
    foreach ($entry in @($result.Output)) {
        Write-Output $entry
    }

    if ($result.ExitCode -ne 0) {
        $message = $result.Text
        if (-not $message) {
            $message = "Docker command failed with exit code $($result.ExitCode)."
        }
        throw $message
    }
}

function Invoke-ZetherionDockerResult {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    if ((Get-ZetherionDockerBackend) -eq "native_windows_docker") {
        return (Invoke-ZetherionNativeDockerResult @DockerArguments)
    }

    return (Invoke-ZetherionWslDockerResult @DockerArguments)
}

function docker {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$RemainingArgs
    )

    Invoke-ZetherionDocker @RemainingArgs
}
