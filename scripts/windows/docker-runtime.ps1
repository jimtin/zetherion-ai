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
$script:ZetherionRequiredDockerMemoryMiB = 98304
$script:ZetherionRequiredDockerSwapMiB = 0
$script:ZetherionRequiredWslMemoryMiB = 98304
$script:ZetherionRequiredWslSwapMiB = 0
$script:ZetherionDockerDesktopContextName = "desktop-linux"
$script:ZetherionDockerDesktopServiceName = "com.docker.service"
$script:ZetherionDockerDesktopStartupTaskName = "ZetherionDockerAutoStart"

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

function Get-ZetherionRequiredDockerMemoryMiB {
    return [int]$script:ZetherionRequiredDockerMemoryMiB
}

function Get-ZetherionRequiredDockerSwapMiB {
    return [int]$script:ZetherionRequiredDockerSwapMiB
}

function Get-ZetherionRequiredWslMemoryMiB {
    return [int]$script:ZetherionRequiredWslMemoryMiB
}

function Get-ZetherionRequiredWslSwapMiB {
    return [int]$script:ZetherionRequiredWslSwapMiB
}

function Get-ZetherionDockerDesktopContextName {
    return [string]$script:ZetherionDockerDesktopContextName
}

function Get-ZetherionDockerDesktopServiceName {
    return [string]$script:ZetherionDockerDesktopServiceName
}

function Get-ZetherionDockerDesktopStartupTaskName {
    return [string]$script:ZetherionDockerDesktopStartupTaskName
}

function Get-ZetherionObjectPropertyValue {
    param(
        [AllowNull()]
        [object]$Object,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        $Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }

    if ($Object.PSObject.Properties.Name -contains $Name) {
        return $Object.$Name
    }

    return $Default
}

function Set-ZetherionObjectPropertyValue {
    param(
        [AllowNull()]
        [object]$Object,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        $Value
    )

    if ($null -eq $Object) {
        return
    }

    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Get-ZetherionIsoTimestampForPath {
    return (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}

function Test-ZetherionScheduledTaskActionContains {
    param(
        [AllowNull()]
        [object]$Task,
        [Parameter(Mandatory = $true)]
        [string]$Needle
    )

    if ($null -eq $Task) {
        return $false
    }

    foreach ($action in @($Task.Actions)) {
        if (
            ($action.Execute -and [string]$action.Execute -like "*$Needle*") -or
            ($action.Arguments -and [string]$action.Arguments -like "*$Needle*")
        ) {
            return $true
        }
    }

    return $false
}

function Set-ZetherionUtf8NoBomContent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Test-ZetherionUtf8Bom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 3) {
        return $false
    }

    return ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
}

function Get-ZetherionDockerDesktopSettingsPath {
    $candidates = New-Object 'System.Collections.Generic.List[string]'

    foreach ($root in @($env:APPDATA, $env:LOCALAPPDATA, $env:USERPROFILE)) {
        if (-not $root) {
            continue
        }

        foreach ($relative in @(
            "Docker\settings-store.json",
            "Docker\settings.json",
            "AppData\Roaming\Docker\settings-store.json",
            "AppData\Roaming\Docker\settings.json"
        )) {
            $candidate = Join-Path $root $relative
            if (-not ($candidates -contains $candidate)) {
                $candidates.Add($candidate) | Out-Null
            }
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    if ($candidates.Count -gt 0) {
        return [string]$candidates[0]
    }

    return "C:\Users\Default\AppData\Roaming\Docker\settings-store.json"
}

function Get-ZetherionDockerDesktopStartupTaskStatus {
    $taskName = Get-ZetherionDockerDesktopStartupTaskName
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return [pscustomobject]@{
            task_name = $taskName
            exists = $false
            enabled = $false
            state = "missing"
            principal_user = ""
            action_matches = $false
        }
    }

    return [pscustomobject]@{
        task_name = $taskName
        exists = $true
        enabled = [bool]$task.Settings.Enabled
        state = [string]$task.State.ToString()
        principal_user = [string]$task.Principal.UserId
        action_matches = [bool](Test-ZetherionScheduledTaskActionContains -Task $task -Needle "Docker Desktop.exe")
    }
}

function Get-ZetherionDockerDesktopExecutablePath {
    foreach ($candidate in @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    return "C:\Program Files\Docker\Docker\Docker Desktop.exe"
}

function Get-ZetherionWslHostConfigPath {
    return Join-Path $env:USERPROFILE ".wslconfig"
}

function ConvertFrom-ZetherionWslSizeToMiB {
    param(
        [AllowEmptyString()]
        [string]$Value
    )

    if (-not $Value) {
        return $null
    }

    $normalized = ([string]$Value).Trim()
    if (-not $normalized) {
        return $null
    }

    $commentIndex = $normalized.IndexOf("#")
    if ($commentIndex -ge 0) {
        $normalized = $normalized.Substring(0, $commentIndex).Trim()
    }
    if (-not $normalized) {
        return $null
    }

    if ($normalized -match '^(?<number>\d+)\s*(?<unit>[KMGTP]?B?)?$') {
        $number = [double]$Matches["number"]
        $unit = [string]$Matches["unit"]
        switch ($unit.ToUpperInvariant()) {
            "" { return [int]$number }
            "B" { return [int][Math]::Floor($number / 1MB) }
            "K" { return [int][Math]::Floor($number / 1024) }
            "KB" { return [int][Math]::Floor($number / 1024) }
            "M" { return [int]$number }
            "MB" { return [int]$number }
            "G" { return [int]($number * 1024) }
            "GB" { return [int]($number * 1024) }
            "T" { return [int]($number * 1024 * 1024) }
            "TB" { return [int]($number * 1024 * 1024) }
        }
    }

    return $null
}

function ConvertTo-ZetherionWslSizeLiteral {
    param([int]$ValueMiB)

    if ($ValueMiB -le 0) {
        return "0"
    }
    if (($ValueMiB % 1024) -eq 0) {
        return "$([int]($ValueMiB / 1024))GB"
    }
    return "${ValueMiB}MB"
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
    $memoryMiB = $null
    $swapMiB = $null
    $processors = $null
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
            continue
        }
        if ($key -eq "memory") {
            $parsedMemoryMiB = ConvertFrom-ZetherionWslSizeToMiB -Value $value
            if ($null -ne $parsedMemoryMiB) {
                $memoryMiB = [int]$parsedMemoryMiB
            }
            continue
        }
        if ($key -eq "swap") {
            $parsedSwapMiB = ConvertFrom-ZetherionWslSizeToMiB -Value $value
            if ($null -ne $parsedSwapMiB) {
                $swapMiB = [int]$parsedSwapMiB
            }
            continue
        }
        if ($key -eq "processors") {
            $parsedProcessors = [int]0
            if ([int]::TryParse(($value -replace '\s+#.*$', '').Trim(), [ref]$parsedProcessors)) {
                $processors = $parsedProcessors
            }
        }
    }

    $recommended = Get-ZetherionRecommendedWslVmIdleTimeoutMs
    $requiredMemoryMiB = Get-ZetherionRequiredWslMemoryMiB
    $requiredSwapMiB = Get-ZetherionRequiredWslSwapMiB
    return [pscustomobject]@{
        path = $path
        exists = [bool]$exists
        raw_lines = @($rawLines)
        memory_mib = if ($null -ne $memoryMiB) { [int]$memoryMiB } else { $null }
        swap_mib = if ($null -ne $swapMiB) { [int]$swapMiB } else { $null }
        processors = if ($null -ne $processors) { [int]$processors } else { $null }
        vm_idle_timeout_ms = if ($null -ne $vmIdleTimeoutMs) { [int64]$vmIdleTimeoutMs } else { $null }
        required_memory_mib = [int]$requiredMemoryMiB
        required_swap_mib = [int]$requiredSwapMiB
        recommended_vm_idle_timeout_ms = [int64]$recommended
        memory_meets_floor = [bool]($null -ne $memoryMiB -and [int]$memoryMiB -ge [int]$requiredMemoryMiB)
        swap_matches_target = [bool]($null -ne $swapMiB -and [int]$swapMiB -eq [int]$requiredSwapMiB)
        passes_idle_timeout = [bool]($null -ne $vmIdleTimeoutMs -and [int64]$vmIdleTimeoutMs -ge [int64]$recommended)
        passes_resource_policy = [bool](
            ($null -ne $memoryMiB -and [int]$memoryMiB -ge [int]$requiredMemoryMiB) -and
            ($null -ne $swapMiB -and [int]$swapMiB -eq [int]$requiredSwapMiB)
        )
    }
}

function Set-ZetherionWslHostConfiguration {
    param(
        [int]$MemoryMiB = (Get-ZetherionRequiredWslMemoryMiB),
        [int]$SwapMiB = (Get-ZetherionRequiredWslSwapMiB),
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
    $wroteMemory = $false
    $wroteSwap = $false
    $wroteVmIdleTimeout = $false
    $memoryLiteral = ConvertTo-ZetherionWslSizeLiteral -ValueMiB $MemoryMiB
    $swapLiteral = ConvertTo-ZetherionWslSizeLiteral -ValueMiB $SwapMiB

    foreach ($rawLine in $existingLines) {
        $line = [string]$rawLine
        $trimmed = $line.Trim()

        if ($trimmed -match "^\[(.+)\]$") {
            if ($inWsl2Section) {
                if (-not $wroteMemory) {
                    $newLines.Add("memory=$memoryLiteral")
                    $wroteMemory = $true
                }
                if (-not $wroteSwap) {
                    $newLines.Add("swap=$swapLiteral")
                    $wroteSwap = $true
                }
                if (-not $wroteVmIdleTimeout) {
                    $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
                    $wroteVmIdleTimeout = $true
                }
            }
            $section = $Matches[1].Trim().ToLowerInvariant()
            $inWsl2Section = ($section -eq "wsl2")
            if ($inWsl2Section) {
                $sawWsl2Section = $true
            }
            $newLines.Add($line)
            continue
        }

        if ($inWsl2Section -and $trimmed -match "^memory\s*=") {
            if (-not $wroteMemory) {
                $newLines.Add("memory=$memoryLiteral")
                $wroteMemory = $true
            }
            continue
        }

        if ($inWsl2Section -and $trimmed -match "^swap\s*=") {
            if (-not $wroteSwap) {
                $newLines.Add("swap=$swapLiteral")
                $wroteSwap = $true
            }
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
        $newLines.Add("memory=$memoryLiteral")
        $newLines.Add("swap=$swapLiteral")
        $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
    }
    elseif ($sawWsl2Section) {
        if (-not $wroteMemory) {
            $newLines.Add("memory=$memoryLiteral")
        }
        if (-not $wroteSwap) {
            $newLines.Add("swap=$swapLiteral")
        }
        if (-not $wroteVmIdleTimeout) {
            $newLines.Add("vmIdleTimeout=$VmIdleTimeoutMs")
        }
    }

    $parent = Split-Path -Parent $path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Set-ZetherionUtf8NoBomContent -Path $path -Content (($newLines -join [Environment]::NewLine) + [Environment]::NewLine)
    return Get-ZetherionWslHostConfig
}

function Set-ZetherionWslHostVmIdleTimeout {
    param(
        [int64]$VmIdleTimeoutMs = (Get-ZetherionRecommendedWslVmIdleTimeoutMs)
    )

    return Set-ZetherionWslHostConfiguration -VmIdleTimeoutMs $VmIdleTimeoutMs
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

function Get-ZetherionDockerDesktopSettings {
    $path = Get-ZetherionDockerDesktopSettingsPath
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{
            path = $path
            exists = $false
            valid_json = $false
            settings = $null
            auto_start = $false
            memory_mib = $null
            swap_mib = $null
            cpus = $null
            auto_pause_timed_activity_seconds = $null
            resource_saver_enabled = $null
        }
    }

    try {
        $raw = Get-Content -LiteralPath $path -Raw -ErrorAction Stop
        $settings = if ($raw.Trim()) {
            $raw | ConvertFrom-Json -ErrorAction Stop
        } else {
            [pscustomobject]@{}
        }
    }
    catch {
        return [pscustomobject]@{
            path = $path
            exists = $true
            valid_json = $false
            settings = $null
            auto_start = $false
            memory_mib = $null
            swap_mib = $null
            cpus = $null
            auto_pause_timed_activity_seconds = $null
            resource_saver_enabled = $null
            error = $_.Exception.Message
        }
    }

    return [pscustomobject]@{
        path = $path
        exists = $true
        valid_json = $true
        settings = $settings
        auto_start = [bool](Get-ZetherionObjectPropertyValue -Object $settings -Name "autoStart" -Default $false)
        memory_mib = (Get-ZetherionObjectPropertyValue -Object $settings -Name "memoryMiB")
        swap_mib = (Get-ZetherionObjectPropertyValue -Object $settings -Name "swapMiB")
        cpus = (Get-ZetherionObjectPropertyValue -Object $settings -Name "cpus")
        auto_pause_timed_activity_seconds = (Get-ZetherionObjectPropertyValue -Object $settings -Name "autoPauseTimedActivitySeconds")
        resource_saver_enabled = if (
            $null -ne (Get-ZetherionObjectPropertyValue -Object $settings -Name "enableResourceSaver")
        ) {
            (Get-ZetherionObjectPropertyValue -Object $settings -Name "enableResourceSaver")
        } else {
            (Get-ZetherionObjectPropertyValue -Object $settings -Name "UseResourceSaver")
        }
    }
}

function Set-ZetherionDockerDesktopDesiredConfiguration {
    param(
        [int]$MemoryMiB = (Get-ZetherionRequiredDockerMemoryMiB),
        [int]$SwapMiB = (Get-ZetherionRequiredDockerSwapMiB),
        [switch]$DisableAutoPause
    )

    $current = Get-ZetherionDockerDesktopSettings
    if (-not $current.exists) {
        return [pscustomobject]@{
            path = $current.path
            exists = $false
            changed = $false
            changed_keys = @()
            backup_path = ""
        }
    }
    if (-not $current.valid_json) {
        throw "Docker Desktop settings file is not valid JSON: $($current.path)"
    }

    $settings = $current.settings
    if ($null -eq $settings) {
        $settings = [pscustomobject]@{}
    }

    $changedKeys = New-Object 'System.Collections.Generic.List[string]'
    $currentAutoStart = [bool](Get-ZetherionObjectPropertyValue -Object $settings -Name "autoStart" -Default $false)
    if (-not $currentAutoStart) {
        Set-ZetherionObjectPropertyValue -Object $settings -Name "autoStart" -Value $true
        $changedKeys.Add("autoStart") | Out-Null
    }

    $currentMemoryMiB = Get-ZetherionObjectPropertyValue -Object $settings -Name "memoryMiB"
    if ($currentMemoryMiB -ne $MemoryMiB) {
        Set-ZetherionObjectPropertyValue -Object $settings -Name "memoryMiB" -Value $MemoryMiB
        $changedKeys.Add("memoryMiB") | Out-Null
    }

    $currentSwapMiB = Get-ZetherionObjectPropertyValue -Object $settings -Name "swapMiB"
    if ($currentSwapMiB -ne $SwapMiB) {
        Set-ZetherionObjectPropertyValue -Object $settings -Name "swapMiB" -Value $SwapMiB
        $changedKeys.Add("swapMiB") | Out-Null
    }

    if ($DisableAutoPause) {
        $currentAutoPause = Get-ZetherionObjectPropertyValue -Object $settings -Name "autoPauseTimedActivitySeconds" -Default 0
        if ($currentAutoPause -ne 0) {
            Set-ZetherionObjectPropertyValue -Object $settings -Name "autoPauseTimedActivitySeconds" -Value 0
            $changedKeys.Add("autoPauseTimedActivitySeconds") | Out-Null
        }

        foreach ($toggleName in @("enableResourceSaver", "resourceSaverEnabled", "UseResourceSaver", "autoPause", "autoPauseEnabled")) {
            if ($settings.PSObject.Properties.Name -contains $toggleName) {
                $currentToggle = [bool](Get-ZetherionObjectPropertyValue -Object $settings -Name $toggleName -Default $false)
                if ($currentToggle) {
                    Set-ZetherionObjectPropertyValue -Object $settings -Name $toggleName -Value $false
                    $changedKeys.Add($toggleName) | Out-Null
                }
            }
        }
    }

    $requiresEncodingRewrite = Test-ZetherionUtf8Bom -Path $current.path

    if ($changedKeys.Count -eq 0 -and -not $requiresEncodingRewrite) {
        return [pscustomobject]@{
            path = $current.path
            exists = $true
            changed = $false
            changed_keys = @()
            backup_path = ""
            encoding_rewritten = $false
        }
    }

    $backupPath = "$($current.path).backup.$(Get-ZetherionIsoTimestampForPath)"
    Copy-Item -LiteralPath $current.path -Destination $backupPath -Force
    $settingsJson = $settings | ConvertTo-Json -Depth 20
    Set-ZetherionUtf8NoBomContent -Path $current.path -Content $settingsJson

    return [pscustomobject]@{
        path = $current.path
        exists = $true
        changed = [bool]($changedKeys.Count -gt 0 -or $requiresEncodingRewrite)
        changed_keys = @($changedKeys.ToArray())
        backup_path = $backupPath
        encoding_rewritten = [bool]$requiresEncodingRewrite
    }
}

function Get-ZetherionDockerDesktopContextStatus {
    $dockerCli = Get-Command "docker.exe" -ErrorAction SilentlyContinue
    $contextName = Get-ZetherionDockerDesktopContextName
    if (-not $dockerCli) {
        return [pscustomobject]@{
            cli_available = $false
            context_name = $contextName
            current_context = ""
            context_exists = $false
            engine_available = $false
        }
    }

    $currentContext = (& $dockerCli.Source context show 2>$null | Out-String).Trim()
    & $dockerCli.Source context inspect $contextName *> $null
    $contextExists = ($LASTEXITCODE -eq 0)

    & $dockerCli.Source --context $contextName info *> $null
    $engineAvailable = ($LASTEXITCODE -eq 0)

    return [pscustomobject]@{
        cli_available = $true
        context_name = $contextName
        current_context = $currentContext
        context_exists = [bool]$contextExists
        engine_available = [bool]$engineAvailable
    }
}

function Get-ZetherionDockerDesktopStatus {
    $settings = Get-ZetherionDockerDesktopSettings
    $serviceName = Get-ZetherionDockerDesktopServiceName
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    $desktopProcesses = @(
        Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
        Get-Process -Name "Docker" -ErrorAction SilentlyContinue
    ) | Where-Object { $null -ne $_ } | Select-Object -Unique -Property Name, Id, Path
    $desktopProcessList = @($desktopProcesses)
    $contextStatus = Get-ZetherionDockerDesktopContextStatus
    $wslRuntimeStatus = Get-ZetherionDockerRuntimeStatus -ExecutionBackend "wsl_docker" -DockerBackend "wsl_docker"
    $startupTaskStatus = Get-ZetherionDockerDesktopStartupTaskStatus
    $wslHostConfig = Get-ZetherionWslHostConfig

    $resourceSaverEnabled = $false
    if ($null -ne $settings.resource_saver_enabled) {
        $resourceSaverEnabled = [bool]$settings.resource_saver_enabled
    }

    return [pscustomobject]@{
        settings_path = [string]$settings.path
        settings_exist = [bool]$settings.exists
        settings_valid_json = [bool]$settings.valid_json
        auto_start = [bool]$settings.auto_start
        memory_mib = if ($null -ne $settings.memory_mib) { [int]$settings.memory_mib } else { $null }
        swap_mib = if ($null -ne $settings.swap_mib) { [int]$settings.swap_mib } else { $null }
        cpus = if ($null -ne $settings.cpus) { [int]$settings.cpus } else { $null }
        auto_pause_timed_activity_seconds = if ($null -ne $settings.auto_pause_timed_activity_seconds) { [int]$settings.auto_pause_timed_activity_seconds } else { $null }
        resource_saver_enabled = [bool]$resourceSaverEnabled
        auto_pause_disabled = [bool](
            ($null -eq $settings.auto_pause_timed_activity_seconds) -or
            ([int]$settings.auto_pause_timed_activity_seconds -eq 0)
        )
        memory_meets_floor = [bool]($null -ne $settings.memory_mib -and [int]$settings.memory_mib -ge (Get-ZetherionRequiredDockerMemoryMiB))
        swap_matches_target = [bool]($null -ne $settings.swap_mib -and [int]$settings.swap_mib -eq (Get-ZetherionRequiredDockerSwapMiB))
        process_running = [bool]($desktopProcessList.Count -gt 0)
        process_names = @($desktopProcessList | ForEach-Object { [string]$_.Name })
        process_ids = @($desktopProcessList | ForEach-Object { [int]$_.Id })
        executable_path = [string](Get-ZetherionDockerDesktopExecutablePath)
        service_name = $serviceName
        service_exists = [bool]($null -ne $service)
        service_status = if ($service) { [string]$service.Status.ToString() } else { "missing" }
        service_start_type = if ($service) { [string]$service.StartType.ToString() } else { "missing" }
        startup_task_name = [string]$startupTaskStatus.task_name
        startup_task_exists = [bool]$startupTaskStatus.exists
        startup_task_enabled = [bool]$startupTaskStatus.enabled
        startup_task_state = [string]$startupTaskStatus.state
        startup_task_principal_user = [string]$startupTaskStatus.principal_user
        startup_task_action_matches = [bool]$startupTaskStatus.action_matches
        docker_cli_available = [bool]$contextStatus.cli_available
        current_context = [string]$contextStatus.current_context
        desktop_linux_context = [string]$contextStatus.context_name
        desktop_linux_context_exists = [bool]$contextStatus.context_exists
        desktop_linux_engine_available = [bool]$contextStatus.engine_available
        wsl_docker_enabled = [bool]$wslRuntimeStatus.enabled
        wsl_docker_active = [bool]$wslRuntimeStatus.active
        wsl_docker_available = [bool]$wslRuntimeStatus.available
        wsl_host_config = $wslHostConfig
        wsl_memory_meets_floor = [bool]$wslHostConfig.memory_meets_floor
        wsl_swap_matches_target = [bool]$wslHostConfig.swap_matches_target
        wsl_vm_idle_timeout_ok = [bool]$wslHostConfig.passes_idle_timeout
        wsl_resources_configured = [bool](
            [bool]$wslHostConfig.passes_resource_policy -and
            [bool]$wslHostConfig.passes_idle_timeout
        )
    }
}

function Start-ZetherionDockerDesktopViaScheduledTask {
    $taskStatus = Get-ZetherionDockerDesktopStartupTaskStatus
    if (-not $taskStatus.exists -or -not $taskStatus.action_matches) {
        return [pscustomobject]@{
            started = $false
            method = "scheduled_task"
            task_name = [string]$taskStatus.task_name
            task_enabled = [bool]$taskStatus.enabled
            task_enabled_changed = $false
            reason = "task_missing_or_mismatched"
        }
    }

    $taskEnabledChanged = $false
    if (-not $taskStatus.enabled) {
        Enable-ScheduledTask -TaskName $taskStatus.task_name -ErrorAction Stop | Out-Null
        $taskEnabledChanged = $true
        $taskStatus = Get-ZetherionDockerDesktopStartupTaskStatus
    }

    Start-ScheduledTask -TaskName $taskStatus.task_name -ErrorAction Stop
    return [pscustomobject]@{
        started = $true
        method = "scheduled_task"
        task_name = [string]$taskStatus.task_name
        task_enabled = [bool]$taskStatus.enabled
        task_enabled_changed = [bool]$taskEnabledChanged
        reason = ""
    }
}

function Start-ZetherionDockerDesktopProcess {
    $executablePath = Get-ZetherionDockerDesktopExecutablePath
    if (-not (Test-Path -LiteralPath $executablePath)) {
        throw "Docker Desktop executable not found at $executablePath"
    }

    try {
        $scheduledTaskResult = Start-ZetherionDockerDesktopViaScheduledTask
        if ($scheduledTaskResult.started) {
            return [pscustomobject]@{
                started = $true
                executable_path = $executablePath
                method = [string]$scheduledTaskResult.method
                task_name = [string]$scheduledTaskResult.task_name
                task_enabled = [bool]$scheduledTaskResult.task_enabled
                task_enabled_changed = [bool]$scheduledTaskResult.task_enabled_changed
            }
        }
    }
    catch {
        # Fall back to direct process start when the scheduled-task path is unavailable.
    }

    Start-Process -FilePath $executablePath -ErrorAction Stop | Out-Null
    return [pscustomobject]@{
        started = $true
        executable_path = $executablePath
        method = "direct_process"
        task_name = ""
        task_enabled = $false
        task_enabled_changed = $false
    }
}

function Wait-ZetherionDockerDesktopEngine {
    param([int]$TimeoutSeconds = 300)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = $null
    while ((Get-Date) -lt $deadline) {
        $lastStatus = Get-ZetherionDockerDesktopStatus
        if ($lastStatus.desktop_linux_engine_available) {
            return $lastStatus
        }
        Start-Sleep -Seconds 5
    }

    if ($null -ne $lastStatus) {
        return $lastStatus
    }

    return Get-ZetherionDockerDesktopStatus
}

function Ensure-ZetherionWslDockerService {
    $actions = New-Object 'System.Collections.Generic.List[string]'

    $enableProbe = Invoke-ZetherionWslCommandResult -Command "systemctl is-enabled docker 2>/dev/null || true"
    $enabled = ($enableProbe.Text -eq "enabled")
    if (-not $enabled) {
        $enableResult = Invoke-ZetherionWslCommandResult -Command "systemctl enable docker >/dev/null 2>&1 || true"
        if ($enableResult.ExitCode -eq 0) {
            $actions.Add("enabled_wsl_docker_service") | Out-Null
        }
        $enableProbe = Invoke-ZetherionWslCommandResult -Command "systemctl is-enabled docker 2>/dev/null || true"
        $enabled = ($enableProbe.Text -eq "enabled")
    }

    $activeProbe = Invoke-ZetherionWslCommandResult -Command "systemctl is-active docker 2>/dev/null || true"
    $active = ($activeProbe.Text -eq "active")
    if (-not $active) {
        $startResult = Invoke-ZetherionWslCommandResult -Command "systemctl start docker >/dev/null 2>&1 || true"
        if ($startResult.ExitCode -eq 0) {
            $actions.Add("started_wsl_docker_service") | Out-Null
        }
        $activeProbe = Invoke-ZetherionWslCommandResult -Command "systemctl is-active docker 2>/dev/null || true"
        $active = ($activeProbe.Text -eq "active")
    }

    return [pscustomobject]@{
        enabled = [bool]$enabled
        active = [bool]$active
        actions = @($actions.ToArray())
    }
}

function Repair-ZetherionDockerDesktopRuntime {
    param(
        [int]$TimeoutSeconds = 300,
        [switch]$RepairSettings,
        [switch]$DisableAutoPause
    )

    $actions = New-Object 'System.Collections.Generic.List[string]'
    $warnings = New-Object 'System.Collections.Generic.List[string]'
    $settingsRepair = $null

    if ($RepairSettings) {
        $settingsRepair = Set-ZetherionDockerDesktopDesiredConfiguration `
            -MemoryMiB (Get-ZetherionRequiredDockerMemoryMiB) `
            -SwapMiB (Get-ZetherionRequiredDockerSwapMiB) `
            -DisableAutoPause:$DisableAutoPause
        if ($settingsRepair.changed) {
            $actions.Add("updated_docker_desktop_settings:$($settingsRepair.changed_keys -join ',')") | Out-Null
        }

        $wslHostConfigBefore = Get-ZetherionWslHostConfig
        $wslHostConfigAfter = Set-ZetherionWslHostConfiguration `
            -MemoryMiB (Get-ZetherionRequiredWslMemoryMiB) `
            -SwapMiB (Get-ZetherionRequiredWslSwapMiB) `
            -VmIdleTimeoutMs (Get-ZetherionRecommendedWslVmIdleTimeoutMs)
        $wslChangedKeys = New-Object 'System.Collections.Generic.List[string]'
        if ($wslHostConfigBefore.memory_mib -ne $wslHostConfigAfter.memory_mib) {
            $wslChangedKeys.Add("memory") | Out-Null
        }
        if ($wslHostConfigBefore.swap_mib -ne $wslHostConfigAfter.swap_mib) {
            $wslChangedKeys.Add("swap") | Out-Null
        }
        if ($wslHostConfigBefore.vm_idle_timeout_ms -ne $wslHostConfigAfter.vm_idle_timeout_ms) {
            $wslChangedKeys.Add("vmIdleTimeout") | Out-Null
        }
        if ($wslChangedKeys.Count -gt 0) {
            $actions.Add("updated_wsl_host_config:$($wslChangedKeys -join ',')") | Out-Null
        }
    }

    $initialStatus = Get-ZetherionDockerDesktopStatus
    if ($initialStatus.service_exists -and $initialStatus.service_status -ne "Running") {
        try {
            Start-Service -Name (Get-ZetherionDockerDesktopServiceName) -ErrorAction Stop
            $actions.Add("started_docker_desktop_service") | Out-Null
        }
        catch {
            $warnings.Add("docker_desktop_service_start_failed:$($_.Exception.Message)") | Out-Null
        }
    }

    $needsProcessRestart = [bool]($settingsRepair -and $settingsRepair.changed -and $initialStatus.process_running)
    if ($needsProcessRestart) {
        foreach ($processId in @($initialStatus.process_ids)) {
            try {
                Stop-Process -Id $processId -Force -ErrorAction Stop
                $actions.Add("stopped_docker_desktop_process:$processId") | Out-Null
            }
            catch {
                $warnings.Add("docker_desktop_process_stop_failed:${processId}:$($_.Exception.Message)") | Out-Null
            }
        }
        Start-Sleep -Seconds 2
    }

    $statusBeforeLaunch = Get-ZetherionDockerDesktopStatus
    if (-not $statusBeforeLaunch.process_running) {
        try {
            $startResult = Start-ZetherionDockerDesktopProcess
            if ($startResult.started) {
                $actions.Add("started_docker_desktop_process:$($startResult.method)") | Out-Null
                if ($startResult.task_enabled_changed) {
                    $actions.Add("enabled_docker_desktop_startup_task:$($startResult.task_name)") | Out-Null
                }
            }
        }
        catch {
            $warnings.Add("docker_desktop_process_start_failed:$($_.Exception.Message)") | Out-Null
        }
    }

    $engineStatus = Wait-ZetherionDockerDesktopEngine -TimeoutSeconds $TimeoutSeconds
    if (-not $engineStatus.desktop_linux_engine_available) {
        $warnings.Add("docker_desktop_linux_engine_unavailable") | Out-Null
    }

    $wslService = Ensure-ZetherionWslDockerService
    foreach ($action in @($wslService.actions)) {
        $actions.Add([string]$action) | Out-Null
    }

    $finalStatus = Get-ZetherionDockerDesktopStatus
    $healthy = [bool](
        $finalStatus.auto_start -and
        $finalStatus.memory_meets_floor -and
        $finalStatus.swap_matches_target -and
        $finalStatus.wsl_resources_configured -and
        $finalStatus.process_running -and
        $finalStatus.desktop_linux_engine_available -and
        $finalStatus.wsl_docker_active
    )

    return [pscustomobject]@{
        success = $healthy
        status = $finalStatus
        settings_repair = $settingsRepair
        actions = @($actions.ToArray())
        warnings = @($warnings.ToArray())
    }
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
    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

        $mountCheckScriptPath = Join-Path (
            [System.IO.Path]::GetTempPath()
        ) ("zetherion-wsl-runtime-mount-" + [guid]::NewGuid().ToString("N") + ".sh")
        $mountCheckScript = @(
            "set -euo pipefail"
            "drive_root=" + (ConvertTo-ZetherionBashLiteral -Value $driveRoot)
            'mount_line=$(mount | grep " on $drive_root " | head -n 1 || true)'
            'if [ -z "$mount_line" ]; then echo "Unable to resolve WSL mount metadata for $drive_root." >&2; exit 1; fi'
            'case "$mount_line" in *metadata*) ;; *) echo "WSL automount for $drive_root must include the metadata option to support writable runtime bind mounts." >&2; exit 1 ;; esac'
        ) -join "`n"
        [System.IO.File]::WriteAllText($mountCheckScriptPath, $mountCheckScript, $utf8NoBom)
        $mountCheckScriptWslPath = ConvertTo-ZetherionWslPath -WindowsPath $mountCheckScriptPath
        $mountCheckResult = Invoke-ZetherionWslCommandResult -User "root" -Command ("bash " + (ConvertTo-ZetherionBashLiteral -Value $mountCheckScriptWslPath))
        if ($mountCheckResult.ExitCode -ne 0) {
            throw $mountCheckResult.Text
        }

        foreach ($relativePath in $RelativePaths) {
            $targetWindowsPath = Join-Path $DeployPath $relativePath
            $targetWslPath = ConvertTo-ZetherionWslPath -WindowsPath $targetWindowsPath
            $preparePathScriptPath = Join-Path (
                [System.IO.Path]::GetTempPath()
            ) ("zetherion-wsl-runtime-path-" + [guid]::NewGuid().ToString("N") + ".sh")
            $preparePathScript = @(
                "set -euo pipefail"
                "target=" + (ConvertTo-ZetherionBashLiteral -Value $targetWslPath)
                'mkdir -p "$target"'
                'chmod -R a+rwX "$target"'
                'touch "$target/.wsl-write-check"'
                'rm -f "$target/.wsl-write-check"'
            ) -join "`n"
            [System.IO.File]::WriteAllText($preparePathScriptPath, $preparePathScript, $utf8NoBom)
            $preparePathScriptWslPath = ConvertTo-ZetherionWslPath -WindowsPath $preparePathScriptPath
            $preparePathResult = Invoke-ZetherionWslCommandResult -User "root" -Command ("bash " + (ConvertTo-ZetherionBashLiteral -Value $preparePathScriptWslPath))
            if ($preparePathResult.ExitCode -ne 0) {
                throw $preparePathResult.Text
            }
            Remove-Item -LiteralPath $preparePathScriptPath -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $mountCheckScriptPath -Force -ErrorAction SilentlyContinue
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
