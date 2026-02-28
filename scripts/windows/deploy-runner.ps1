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
    [string]$OutputPath = "deploy-result.json"
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
    target_sha = $TargetSha
    target_ref = $TargetRef
    previous_sha = ""
    deployed_sha = ""
    deployed_at = [DateTime]::UtcNow.ToString("o")
    status = "failed"
    error = ""
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
        Sync-CgsSharedSecret -RepositoryPath $DeployPath

        $deployedSha = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve deployed SHA after checkout."
        }
        if ($deployedSha -ne $TargetSha) {
            throw "Resolved deployed SHA '$deployedSha' did not match target '$TargetSha'."
        }

        docker compose up -d --build

        $result.deployed_sha = $deployedSha
        $result.status = "deployed"
    } finally {
        Pop-Location
    }
} catch {
    $result.error = $_.Exception.Message
    Write-DeployResult -Result $result -Path $OutputPath
    throw
} finally {
    if ($lockCreated -and (Test-Path $LockPath)) {
        Remove-Item -Path $LockPath -Force
    }
}

Write-DeployResult -Result $result -Path $OutputPath
