param(
    [Parameter(Mandatory = $true)]
    [string]$DeployPath,
    [Parameter(Mandatory = $true)]
    [string]$LastGoodShaPath,
    [Parameter(Mandatory = $false)]
    [string]$LockPath = "C:\ZetherionAI\data\deploy.lock",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "rollback-result.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Write-RollbackResult {
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
        [System.Collections.Generic.List[string]]$Lines,
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

    if ($updatedKeys.Count -gt 0) {
        Set-Content -Path $rootEnvPath -Value $lines -Encoding utf8
    }

    return @($updatedKeys)
}

function Sync-CgsSharedSecret {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath
    )

    $rootEnvPath = Join-Path $RepositoryPath ".env"
    $secret = Get-EnvValueFromFile -Path $rootEnvPath -Keys @("ZETHERION_SKILLS_API_SECRET", "SKILLS_API_SECRET")
    if (-not $secret) {
        throw "Missing ZETHERION_SKILLS_API_SECRET/SKILLS_API_SECRET in $rootEnvPath"
    }

    $cgsDirectory = Join-Path $RepositoryPath "cgs"
    if (-not (Test-Path $cgsDirectory)) {
        New-Item -ItemType Directory -Path $cgsDirectory -Force | Out-Null
    }

    $cgsEnvLocalPath = Join-Path $cgsDirectory ".env.local"
    $cgsEnvLines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path $cgsEnvLocalPath) {
        foreach ($line in Get-Content -Path $cgsEnvLocalPath) {
            $cgsEnvLines.Add($line)
        }
    }

    Set-OrAddEnvLine -Lines $cgsEnvLines -Key "SKILLS_API_SECRET" -Value $secret
    Set-OrAddEnvLine -Lines $cgsEnvLines -Key "ZETHERION_SKILLS_API_SECRET" -Value $secret

    Set-Content -Path $cgsEnvLocalPath -Value $cgsEnvLines -Encoding utf8
}

$result = [ordered]@{
    rolled_back_sha = ""
    rolled_back_at = [DateTime]::UtcNow.ToString("o")
    status = "failed"
    error = ""
}

$lockCreated = $false

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }

    if (-not (Test-Path $LastGoodShaPath)) {
        throw "Last good SHA file not found: $LastGoodShaPath"
    }

    if (Test-Path $LockPath) {
        throw "Deployment lock exists: $LockPath"
    }

    $lastGoodSha = (Get-Content $LastGoodShaPath -Raw).Trim()
    if (-not $lastGoodSha) {
        throw "Last good SHA file is empty: $LastGoodShaPath"
    }

    $lockParent = Split-Path -Parent $LockPath
    if ($lockParent -and -not (Test-Path $lockParent)) {
        New-Item -ItemType Directory -Path $lockParent -Force | Out-Null
    }
    Set-Content -Path $LockPath -Value "pid=$PID started=$([DateTime]::UtcNow.ToString('o'))`n" -Encoding utf8
    $lockCreated = $true

    Push-Location $DeployPath
    try {
        # Normalize tracked files and only clean drift-prone script paths.
        # Do not wipe runtime state such as .env, data/, or logs/.
        Invoke-Git @("reset", "--hard", "HEAD")
        Invoke-Git @("clean", "-ffdx", "--", "scripts/windows")
        Invoke-Git @("fetch", "--prune", "--force", "origin")
        Invoke-Git @("fetch", "--depth=1", "--force", "origin", $lastGoodSha)
        Invoke-Git @("checkout", "--detach", "--force", $lastGoodSha)
        $bootstrappedKeys = Ensure-RequiredRuntimeEnv -RepositoryPath $DeployPath
        if ($bootstrappedKeys.Count -gt 0) {
            Write-Output "Bootstrapped runtime env keys during rollback: $($bootstrappedKeys -join ', ')"
        }
        Sync-CgsSharedSecret -RepositoryPath $DeployPath
        docker compose up -d --build
    } finally {
        Pop-Location
    }

    $result.rolled_back_sha = $lastGoodSha
    $result.status = "rolled_back"
} catch {
    $result.error = $_.Exception.Message
    Write-RollbackResult -Result $result -Path $OutputPath
    throw
} finally {
    if ($lockCreated -and (Test-Path $LockPath)) {
        Remove-Item -Path $LockPath -Force
    }
}

Write-RollbackResult -Result $result -Path $OutputPath
