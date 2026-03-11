param(
    [Parameter(Mandatory = $true)]
    [string]$DeployPath,
    [Parameter(Mandatory = $true)]
    [string]$TargetSha,
    [Parameter(Mandatory = $false)]
    [string]$TargetRef = "",
    [Parameter(Mandatory = $false)]
    [string]$LockPath = "C:\ZetherionAI\data\deploy.lock",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "deploy-result.json",
    [Parameter(Mandatory = $false)]
    [string]$DiagnosticsPath = "deploy-container-diagnostics.json",
    [Parameter(Mandatory = $false)]
    [string]$DiagnosticsLogsPath = "deploy-container-logs.txt"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Write-DeployResult {
    param(
        [object]$Result,
        [string]$Path
    )
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Result | ConvertTo-Json -Depth 8 | Out-File $Path -Encoding utf8
}

function Write-TextFile {
    param(
        [string]$Path,
        [string]$Content
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Content | Out-File $Path -Encoding utf8
}

function Collect-DeployDiagnostics {
    param(
        [string]$RepositoryPath,
        [string]$DiagnosticsPath,
        [string]$DiagnosticsLogsPath
    )

    $generatedAt = [DateTime]::UtcNow.ToString("o")
    $composePs = ""
    $dockerPs = ""
    $containerStates = @()
    $failedContainers = @()
    $logsBuilder = New-Object System.Text.StringBuilder

    Push-Location $RepositoryPath
    try {
        $composePs = (docker compose ps 2>&1 | Out-String)
        $dockerPs = (docker ps --format "table {{.Names}}`t{{.Status}}`t{{.Image}}" 2>&1 | Out-String)

        $containerNames = @(
            docker ps -a --format "{{.Names}}" 2>$null |
                Where-Object { $_ -and $_ -like "zetherion-ai-*" }
        )

        foreach ($container in $containerNames) {
            $stateRaw = docker inspect --format "{{json .State}}" $container 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $stateRaw) {
                continue
            }

            $state = $stateRaw | ConvertFrom-Json
            $status = [string]$state.Status
            $exitCode = 0
            if ($state.PSObject.Properties.Name -contains "ExitCode") {
                $exitCode = [int]$state.ExitCode
            }

            $healthStatus = ""
            if (
                ($state.PSObject.Properties.Name -contains "Health") -and
                $null -ne $state.Health -and
                ($state.Health.PSObject.Properties.Name -contains "Status")
            ) {
                $healthStatus = [string]$state.Health.Status
            }

            $containerState = [ordered]@{
                name = $container
                status = $status
                health = $healthStatus
                exit_code = $exitCode
            }
            $containerStates += $containerState

            $isFailed = $false
            if ($status -ne "running") {
                $isFailed = $true
            } elseif ($healthStatus -eq "unhealthy") {
                $isFailed = $true
            } elseif ($exitCode -ne 0) {
                $isFailed = $true
            }

            if (-not $isFailed) {
                continue
            }

            $failedContainers += $containerState
            [void]$logsBuilder.AppendLine("===== $container =====")
            [void]$logsBuilder.AppendLine("status=$status health=$healthStatus exit_code=$exitCode")
            $tail = (docker logs --tail 200 $container 2>&1 | Out-String)
            [void]$logsBuilder.AppendLine($tail)
            [void]$logsBuilder.AppendLine()
        }
    } finally {
        Pop-Location
    }

    $payload = [ordered]@{
        generated_at = $generatedAt
        compose_ps = $composePs
        docker_ps = $dockerPs
        container_states = $containerStates
        failed_containers = $failedContainers
    }

    $payload | ConvertTo-Json -Depth 8 | Out-File $DiagnosticsPath -Encoding utf8
    Write-TextFile -Path $DiagnosticsLogsPath -Content $logsBuilder.ToString()

    return [pscustomobject]@{
        diagnostics_path = $DiagnosticsPath
        diagnostics_logs_path = $DiagnosticsLogsPath
        failed_containers = $failedContainers
    }
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

function Get-OptionalComposeProfiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath
    )

    $rootEnvPath = Join-Path $RepositoryPath ".env"
    if (-not (Test-Path $rootEnvPath)) {
        throw "Required env file not found: $rootEnvPath"
    }

    $profiles = New-Object 'System.Collections.Generic.List[string]'

    $cloudflareToken = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CLOUDFLARE_TUNNEL_TOKEN")
    if ($cloudflareToken) {
        $profiles.Add("cloudflared")
    }

    $whatsappEnabledRaw = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_ENABLED")
    $whatsappEnabled = $false
    if ($whatsappEnabledRaw) {
        $whatsappEnabled = @("1", "true", "yes", "on") -contains $whatsappEnabledRaw.Trim().ToLowerInvariant()
    }
    $whatsappSigningSecret = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_SIGNING_SECRET")
    $whatsappStateKey = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_STATE_KEY")
    $whatsappTenantId = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_TENANT_ID")
    $whatsappIngestUrl = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("WHATSAPP_BRIDGE_INGEST_URL")

    if ($whatsappEnabled -and $whatsappSigningSecret -and $whatsappStateKey -and $whatsappTenantId -and $whatsappIngestUrl) {
        $profiles.Add("whatsapp-bridge")
    }

    return [string[]]$profiles.ToArray()
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
        Set-OrAddEnvLine -Lines $lines -Key "CGS_AUTH_JWKS_URL" -Value "https://example.com/.well-known/jwks.json"
        $updatedKeys.Add("CGS_AUTH_JWKS_URL")
    }

    $cgsIssuer = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CGS_AUTH_ISSUER")
    if (-not $cgsIssuer) {
        Set-OrAddEnvLine -Lines $lines -Key "CGS_AUTH_ISSUER" -Value "cgs-placeholder-issuer"
        $updatedKeys.Add("CGS_AUTH_ISSUER")
    }

    $cgsAudience = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("CGS_AUTH_AUDIENCE")
    if (-not $cgsAudience) {
        Set-OrAddEnvLine -Lines $lines -Key "CGS_AUTH_AUDIENCE" -Value "cgs-placeholder-audience"
        $updatedKeys.Add("CGS_AUTH_AUDIENCE")
    }

    $embeddingsBackend = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("EMBEDDINGS_BACKEND")
    if (-not $embeddingsBackend) {
        Set-OrAddEnvLine -Lines $lines -Key "EMBEDDINGS_BACKEND" -Value "openai"
        $updatedKeys.Add("EMBEDDINGS_BACKEND")
    }

    $openaiEmbeddingModel = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("OPENAI_EMBEDDING_MODEL")
    if (-not $openaiEmbeddingModel) {
        Set-OrAddEnvLine -Lines $lines -Key "OPENAI_EMBEDDING_MODEL" -Value "text-embedding-3-large"
        $updatedKeys.Add("OPENAI_EMBEDDING_MODEL")
    }

    $openaiEmbeddingDimensions = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("OPENAI_EMBEDDING_DIMENSIONS")
    if (-not $openaiEmbeddingDimensions) {
        Set-OrAddEnvLine -Lines $lines -Key "OPENAI_EMBEDDING_DIMENSIONS" -Value "3072"
        $updatedKeys.Add("OPENAI_EMBEDDING_DIMENSIONS")
    }

    if ($updatedKeys.Count -gt 0) {
        Set-Content -Path $rootEnvPath -Value $lines -Encoding utf8
    }

    return [string[]]$updatedKeys.ToArray()
}

$result = [ordered]@{
    target_sha = $TargetSha
    target_ref = $TargetRef
    previous_sha = ""
    deployed_sha = ""
    deployed_at = [DateTime]::UtcNow.ToString("o")
    status = "failed"
    error = ""
    diagnostics_path = ""
    diagnostics_logs_path = ""
    failed_services = @()
}

$lockCreated = $false

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }

    if (Test-Path $LockPath) {
        throw "Deployment lock exists: $LockPath"
    }

    $lockParent = Split-Path -Parent $LockPath
    if ($lockParent -and -not (Test-Path $lockParent)) {
        New-Item -ItemType Directory -Path $lockParent -Force | Out-Null
    }
    Set-Content -Path $LockPath -Value "pid=$PID started=$([DateTime]::UtcNow.ToString('o'))`n" -Encoding utf8
    $lockCreated = $true

    Push-Location $DeployPath
    try {
        Invoke-Git @("rev-parse", "--is-inside-work-tree")
        $previousSha = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve current deploy SHA."
        }
        $result.previous_sha = $previousSha

        # Normalize tracked files and only clean drift-prone script paths.
        # Do not wipe runtime state such as .env, data/, or logs/.
        Invoke-Git @("reset", "--hard", "HEAD")
        Invoke-Git @("clean", "-ffdx", "--", "scripts/windows")
        Invoke-Git @("fetch", "--prune", "--force", "origin")
        Invoke-Git @("fetch", "--depth=1", "--force", "origin", $TargetSha)
        Invoke-Git @("checkout", "--detach", "--force", $TargetSha)
        $bootstrappedKeys = @(Ensure-RequiredRuntimeEnv -RepositoryPath $DeployPath)
        if ($bootstrappedKeys.Count -gt 0) {
            Write-Output "Bootstrapped runtime env keys: $($bootstrappedKeys -join ', ')"
        }
        $composeProfiles = @(Get-OptionalComposeProfiles -RepositoryPath $DeployPath)
        if ($composeProfiles.Count -gt 0) {
            Write-Output "Active optional compose profiles: $($composeProfiles -join ', ')"
        } else {
            Write-Output "Active optional compose profiles: none"
        }

        $deployedSha = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve deployed SHA after checkout."
        }
        if ($deployedSha -ne $TargetSha) {
            throw "Resolved deployed SHA '$deployedSha' did not match target '$TargetSha'."
        }

        $composeArgs = New-Object 'System.Collections.Generic.List[string]'
        $composeArgs.Add("compose")
        foreach ($profile in $composeProfiles) {
            $composeArgs.Add("--profile")
            $composeArgs.Add($profile)
        }
        $composeArgs.Add("up")
        $composeArgs.Add("-d")
        $composeArgs.Add("--build")
        $composeArgs.Add("--remove-orphans")

        & docker @composeArgs
        $composeExitCode = $LASTEXITCODE
        if ($composeExitCode -ne 0) {
            throw "docker compose up -d --build failed with exit code $composeExitCode"
        }

        $result.deployed_sha = $deployedSha
        $result.status = "deployed"
    } finally {
        Pop-Location
    }
} catch {
    $result.error = $_.Exception.Message
    try {
        $diagnostics = Collect-DeployDiagnostics `
            -RepositoryPath $DeployPath `
            -DiagnosticsPath $DiagnosticsPath `
            -DiagnosticsLogsPath $DiagnosticsLogsPath
        $result.diagnostics_path = $diagnostics.diagnostics_path
        $result.diagnostics_logs_path = $diagnostics.diagnostics_logs_path
        $result.failed_services = @($diagnostics.failed_containers | ForEach-Object { $_.name })
    } catch {
        $result.diagnostics_path = $DiagnosticsPath
        $result.diagnostics_logs_path = $DiagnosticsLogsPath
        $result.failed_services = @()
    }
    Write-DeployResult -Result $result -Path $OutputPath
    throw
} finally {
    if ($lockCreated -and (Test-Path $LockPath)) {
        Remove-Item -Path $LockPath -Force
    }
}

Write-DeployResult -Result $result -Path $OutputPath
