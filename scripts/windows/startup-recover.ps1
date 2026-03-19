param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu",
    [Parameter(Mandatory = $false)]
    [string]$LastGoodShaPath = "C:\ZetherionAI\data\last-good-sha.txt",
    [Parameter(Mandatory = $false)]
    [string]$LockPath = "C:\ZetherionAI\data\deploy.lock",
    [Parameter(Mandatory = $false)]
    [int]$LockStaleMinutes = 45,
    [Parameter(Mandatory = $false)]
    [int]$MaxNetworkWaitSeconds = 300,
    [Parameter(Mandatory = $false)]
    [int]$MaxDockerWaitSeconds = 300,
    [Parameter(Mandatory = $false)]
    [string]$ReceiptPath = "C:\ZetherionAI\data\boot-recovery-receipt.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Write-BootReceipt {
    param([object]$Receipt, [string]$Path)
    Ensure-ParentDir -Path $Path
    $Receipt | ConvertTo-Json -Depth 10 | Out-File $Path -Encoding utf8
}

function Get-CurrentRepoSha {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return ""
    }
    Push-Location $Path
    try {
        $sha = (git rev-parse HEAD 2>$null).Trim()
        return $sha
    }
    catch {
        return ""
    }
    finally {
        Pop-Location
    }
}

function Wait-ForNetwork {
    param([int]$TimeoutSeconds)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $ipConfig = Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object {
                $_.NetAdapter.Status -eq "Up" -and $_.IPv4DefaultGateway -ne $null
            }
            if ($ipConfig) {
                return $true
            }
        }
        catch {
            # Ignore transient probe errors while waiting.
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Wait-ForDocker {
    param([int]$TimeoutSeconds, [ref]$ActionsTaken)

    $repair = Repair-ZetherionDockerDesktopRuntime -TimeoutSeconds $TimeoutSeconds -RepairSettings -DisableAutoPause
    foreach ($action in @($repair.actions)) {
        $ActionsTaken.Value += [string]$action
    }
    foreach ($warning in @($repair.warnings)) {
        $ActionsTaken.Value += "docker_warning:$warning"
    }
    if ($repair.settings_repair -and $repair.settings_repair.changed) {
        $ActionsTaken.Value += "docker_settings_repaired"
    }

    return [bool]$repair.success
}

function Get-EnvValueFromFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string[]]$Keys
    )

    if (-not (Test-Path $Path)) {
        throw "Required env file not found: $Path"
    }

    $lines = Get-Content -Path $Path
    foreach ($key in $Keys) {
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            $line = $lines[$i]
            if ($line -match "^\s*#") {
                continue
            }
            if ($line -notmatch "^\s*$([Regex]::Escape($key))\s*=") {
                continue
            }

            $separatorIndex = $line.IndexOf("=")
            if ($separatorIndex -lt 0) {
                continue
            }
            $value = $line.Substring($separatorIndex + 1).Trim()
            if (
                ($value.StartsWith("'") -and $value.EndsWith("'")) -or
                ($value.StartsWith('"') -and $value.EndsWith('"'))
            ) {
                if ($value.Length -ge 2) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            if ($value) {
                return $value
            }
        }
    }

    return ""
}

function Test-TruthyValue {
    param([string]$Value)

    if (-not $Value) {
        return $false
    }

    switch ($Value.Trim().ToLowerInvariant()) {
        "1" { return $true }
        "true" { return $true }
        "yes" { return $true }
        "on" { return $true }
        default { return $false }
    }
}

function Set-OrAddEnvLine {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.IList]$Lines,
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $pattern = "^\s*$([Regex]::Escape($Key))\s*="
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $pattern) {
            $Lines[$i] = "$Key=$Value"
            return
        }
    }

    $Lines.Add("$Key=$Value")
}

function Get-OptionalComposeProfiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath
    )

    $profiles = New-Object 'System.Collections.Generic.List[string]'
    $rootEnvPath = Join-Path $RepositoryPath ".env"
    if (-not (Test-Path $rootEnvPath)) {
        return [string[]]$profiles.ToArray()
    }

    $cloudflareToken = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CLOUDFLARE_TUNNEL_TOKEN")
    if ($cloudflareToken) {
        $profiles.Add("cloudflared")
    }

    $whatsappEnabled = Test-TruthyValue -Value (Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_ENABLED"))
    $whatsappSigningSecret = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_SIGNING_SECRET")
    $whatsappStateKey = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_STATE_KEY")
    $whatsappTenantId = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_TENANT_ID")
    $whatsappIngestUrl = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_INGEST_URL")

    if ($whatsappEnabled -and $whatsappSigningSecret -and $whatsappStateKey -and $whatsappTenantId -and $whatsappIngestUrl) {
        $profiles.Add("whatsapp-bridge")
    }

    $ollamaEnabled = Test-TruthyValue -Value (Get-EnvValueFromFile -Path $rootEnvPath -Keys @("ENABLE_OLLAMA_RUNTIME"))
    if ($ollamaEnabled) {
        $profiles.Add("ollama")
    }

    return [string[]]$profiles.ToArray()
}

function New-RandomUrlSafeSecret {
    param([int]$NumBytes = 48)

    if ($NumBytes -lt 16) {
        $NumBytes = 16
    }

    $bytes = New-Object byte[] $NumBytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }

    $value = [Convert]::ToBase64String($bytes).TrimEnd("=")
    return $value.Replace("+", "-").Replace("/", "_")
}

function Ensure-RequiredRuntimeEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath
    )

    $rootEnvPath = Join-Path $RepositoryPath ".env"
    if (-not (Test-Path $rootEnvPath)) {
        throw "Required env file not found: $rootEnvPath"
    }

    $lines = New-Object 'System.Collections.Generic.List[string]'
    foreach ($line in Get-Content -Path $rootEnvPath) {
        $lines.Add($line)
    }

    $updatedKeys = New-Object 'System.Collections.Generic.List[string]'

    $apiJwtSecret = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("API_JWT_SECRET")
    if (-not $apiJwtSecret) {
        Set-OrAddEnvLine -Lines $lines -Key "API_JWT_SECRET" -Value (New-RandomUrlSafeSecret)
        $updatedKeys.Add("API_JWT_SECRET")
    }

    $cgsJwks = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CGS_AUTH_JWKS_URL")
    if (-not $cgsJwks) {
        Write-Warning "CGS_AUTH_JWKS_URL is missing from $rootEnvPath. Sync the real value from the CGS/Vercel environment before CGS gateway validation."
    }

    $cgsIssuer = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CGS_AUTH_ISSUER")
    if (-not $cgsIssuer) {
        Write-Warning "CGS_AUTH_ISSUER is missing from $rootEnvPath. Sync the real value from the CGS/Vercel environment when RS256 validation is required."
    }

    $cgsAudience = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CGS_AUTH_AUDIENCE")
    if (-not $cgsAudience) {
        Write-Warning "CGS_AUTH_AUDIENCE is missing from $rootEnvPath. This is optional unless the gateway enforces audience matching."
    }

    $ollamaEnabled = Test-TruthyValue -Value (Get-EnvValueFromFile -Path $rootEnvPath -Keys @("ENABLE_OLLAMA_RUNTIME"))
    if (-not $ollamaEnabled) {
        $routerBackend = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("ROUTER_BACKEND")
        if ((-not $routerBackend) -or ($routerBackend -eq "ollama")) {
            Set-OrAddEnvLine -Lines $lines -Key "ROUTER_BACKEND" -Value "gemini"
            $updatedKeys.Add("ROUTER_BACKEND")
        }
    }

    $embeddingsBackend = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("EMBEDDINGS_BACKEND")
    if ((-not $embeddingsBackend) -or ((-not $ollamaEnabled) -and ($embeddingsBackend -eq "ollama"))) {
        Set-OrAddEnvLine -Lines $lines -Key "EMBEDDINGS_BACKEND" -Value "openai"
        $updatedKeys.Add("EMBEDDINGS_BACKEND")
    }

    if ($updatedKeys.Count -gt 0) {
        Set-Content -Path $rootEnvPath -Value $lines -Encoding utf8
    }

    return [string[]]$updatedKeys.ToArray()
}

$bootId = [guid]::NewGuid().ToString()
$bootStartedAt = [DateTime]::UtcNow
$actionsTaken = @()
$activeChecks = [ordered]@{
    containers_healthy = $false
    bot_startup_markers = $false
    postgres_model_keys = $false
    fallback_probe = $false
}
$status = "failed"
$errorText = ""

$preBootSha = Get-CurrentRepoSha -Path $DeployPath
$postRecoverySha = $preBootSha

$verifyScriptPath = Join-Path $DeployPath "scripts\windows\verify-runtime.ps1"
$rollbackScriptPath = Join-Path $DeployPath "scripts\windows\rollback-last-good.ps1"
$tempVerifyPath = Join-Path $env:TEMP "startup-verify-result.json"
$tempRollbackPath = Join-Path $env:TEMP "startup-rollback-result.json"
$tempPostRollbackVerifyPath = Join-Path $env:TEMP "startup-post-rollback-verify-result.json"

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }
    if (-not (Test-Path $verifyScriptPath)) {
        throw "Verification script not found: $verifyScriptPath"
    }
    if (-not (Test-Path $rollbackScriptPath)) {
        throw "Rollback script not found: $rollbackScriptPath"
    }

    if (-not (Wait-ForNetwork -TimeoutSeconds $MaxNetworkWaitSeconds)) {
        throw "Network readiness check timed out after $MaxNetworkWaitSeconds seconds."
    }
    $actionsTaken += "network_ready"

    if (-not (Wait-ForDocker -TimeoutSeconds $MaxDockerWaitSeconds -ActionsTaken ([ref]$actionsTaken))) {
        throw "Docker readiness check timed out after $MaxDockerWaitSeconds seconds."
    }
    $actionsTaken += "docker_ready"

    if (Test-Path $LockPath) {
        $lockItem = Get-Item $LockPath -ErrorAction SilentlyContinue
        if ($lockItem) {
            $ageMinutes = ((Get-Date) - $lockItem.LastWriteTime).TotalMinutes
            if ($ageMinutes -ge $LockStaleMinutes) {
                Remove-Item -Path $LockPath -Force
                $actionsTaken += "removed_stale_lock"
            } else {
                throw "Active deployment lock present at $LockPath (age=$([Math]::Round($ageMinutes, 1)) minutes)."
            }
        }
    }

    Push-Location $DeployPath
    try {
        $bootstrappedKeys = @(Ensure-RequiredRuntimeEnv -RepositoryPath $DeployPath)
        if ($bootstrappedKeys.Count -gt 0) {
            $actionsTaken += "bootstrapped_runtime_env_keys:$($bootstrappedKeys -join ',')"
        }
        try {
            Ensure-ZetherionWslRuntimePaths -DeployPath $DeployPath
            $actionsTaken += "wsl_runtime_paths_ready"
        }
        catch {
            $actionsTaken += "wsl_runtime_paths_warning:$($_.Exception.Message)"
        }
        $composeArgs = New-Object 'System.Collections.Generic.List[string]'
        $composeArgs.Add("compose")
        foreach ($profile in @(Get-OptionalComposeProfiles -RepositoryPath $DeployPath)) {
            $composeArgs.Add("--profile")
            $composeArgs.Add($profile)
        }
        $composeArgs.Add("up")
        $composeArgs.Add("-d")
        $composeArgs.Add("--remove-orphans")
        & docker @composeArgs
    }
    finally {
        Pop-Location
    }
    $actionsTaken += "compose_up"

    & $verifyScriptPath -DeployPath $DeployPath -OutputPath $tempVerifyPath
    if ($LASTEXITCODE -eq 0) {
        if (Test-Path $tempVerifyPath) {
            $verifyResult = Get-Content $tempVerifyPath -Raw | ConvertFrom-Json
            $activeChecks = [ordered]@{
                containers_healthy = [bool]$verifyResult.checks.containers_healthy
                bot_startup_markers = [bool]$verifyResult.checks.bot_startup_markers
                postgres_model_keys = [bool]$verifyResult.checks.postgres_model_keys
                fallback_probe = [bool]$verifyResult.checks.fallback_probe
            }
        }
        $status = "success"
        $actionsTaken += "verify_success"
    } else {
        $actionsTaken += "verify_failed_initial"

        & $rollbackScriptPath `
            -DeployPath $DeployPath `
            -LastGoodShaPath $LastGoodShaPath `
            -LockPath $LockPath `
            -OutputPath $tempRollbackPath

        if ($LASTEXITCODE -ne 0) {
            throw "Rollback failed during startup recovery."
        }

        $actionsTaken += "rollback_executed"

        & $verifyScriptPath -DeployPath $DeployPath -OutputPath $tempPostRollbackVerifyPath
        if ($LASTEXITCODE -eq 0) {
            if (Test-Path $tempPostRollbackVerifyPath) {
                $postRollbackVerify = Get-Content $tempPostRollbackVerifyPath -Raw | ConvertFrom-Json
                $activeChecks = [ordered]@{
                    containers_healthy = [bool]$postRollbackVerify.checks.containers_healthy
                    bot_startup_markers = [bool]$postRollbackVerify.checks.bot_startup_markers
                    postgres_model_keys = [bool]$postRollbackVerify.checks.postgres_model_keys
                    fallback_probe = [bool]$postRollbackVerify.checks.fallback_probe
                }
            }
            $status = "rolled_back"
            $actionsTaken += "verify_success_after_rollback"
        } else {
            if (Test-Path $tempPostRollbackVerifyPath) {
                $postRollbackVerify = Get-Content $tempPostRollbackVerifyPath -Raw | ConvertFrom-Json
                $activeChecks = [ordered]@{
                    containers_healthy = [bool]$postRollbackVerify.checks.containers_healthy
                    bot_startup_markers = [bool]$postRollbackVerify.checks.bot_startup_markers
                    postgres_model_keys = [bool]$postRollbackVerify.checks.postgres_model_keys
                    fallback_probe = [bool]$postRollbackVerify.checks.fallback_probe
                }
            }
            throw "Runtime verification still failing after rollback."
        }
    }
}
catch {
    $errorText = $_.Exception.Message
    if ($status -ne "rolled_back") {
        $status = "failed"
    }
    $actionsTaken += "startup_recovery_error"
}
finally {
    $postRecoverySha = Get-CurrentRepoSha -Path $DeployPath

    $receipt = [ordered]@{
        boot_id = $bootId
        boot_started_at = $bootStartedAt.ToString("o")
        boot_completed_at = [DateTime]::UtcNow.ToString("o")
        pre_boot_sha = $preBootSha
        post_recovery_sha = $postRecoverySha
        checks = $activeChecks
        actions = $actionsTaken
        status = $status
        error = $errorText
    }

    Write-BootReceipt -Receipt $receipt -Path $ReceiptPath
}

if ($status -eq "success" -or $status -eq "rolled_back") {
    exit 0
}

exit 1
