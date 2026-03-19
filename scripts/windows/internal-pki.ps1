function Get-InternalPkiEnvDefaults {
    param([string]$DeployPath = "C:\ZetherionAI")

    $certRoot = Join-Path $DeployPath "data\certs"
    return [ordered]@{
        "INTERNAL_TLS_CA_PATH" = (Join-Path $certRoot "internal\ca.pem")
        "INTERNAL_TLS_CLIENT_CERT_PATH" = (Join-Path $certRoot "internal\client.pem")
        "INTERNAL_TLS_CLIENT_KEY_PATH" = (Join-Path $certRoot "internal\client-key.pem")
        "API_TLS_CERT_PATH" = (Join-Path $certRoot "internal\api.pem")
        "API_TLS_KEY_PATH" = (Join-Path $certRoot "internal\api-key.pem")
        "SKILLS_TLS_CERT_PATH" = (Join-Path $certRoot "internal\skills.pem")
        "SKILLS_TLS_KEY_PATH" = (Join-Path $certRoot "internal\skills-key.pem")
        "CGS_GATEWAY_TLS_CERT_PATH" = (Join-Path $certRoot "internal\cgs-gateway.pem")
        "CGS_GATEWAY_TLS_KEY_PATH" = (Join-Path $certRoot "internal\cgs-gateway-key.pem")
        "UPDATER_TLS_CERT_PATH" = (Join-Path $certRoot "internal\updater.pem")
        "UPDATER_TLS_KEY_PATH" = (Join-Path $certRoot "internal\updater-key.pem")
        "UPDATER_TLS_REQUIRE_CLIENT_CERT" = "true"
        "DEV_AGENT_API_TLS_CERT_PATH" = (Join-Path $certRoot "internal\dev-agent.pem")
        "DEV_AGENT_API_TLS_KEY_PATH" = (Join-Path $certRoot "internal\dev-agent-key.pem")
        "DEV_AGENT_INTERNAL_TLS_CA_PATH" = (Join-Path $certRoot "internal\ca.pem")
        "DEV_AGENT_API_REQUIRE_CLIENT_CERT" = "true"
        "POSTGRES_TLS_CA_PATH" = (Join-Path $certRoot "postgres\ca.pem")
        "POSTGRES_TLS_CERT_PATH" = (Join-Path $certRoot "postgres\client.pem")
        "POSTGRES_TLS_KEY_PATH" = (Join-Path $certRoot "postgres\client-key.pem")
        "QDRANT_CERT_PATH" = (Join-Path $certRoot "qdrant\ca.pem")
    }
}

function Get-InternalPkiSchemaVersion {
    return "2"
}

function Get-InternalPkiVersionFilePath {
    param([string]$DeployPath = "C:\ZetherionAI")

    return (Join-Path (Join-Path $DeployPath "data\certs") "version.txt")
}

function Test-InternalPkiSchemaCurrent {
    param([string]$DeployPath = "C:\ZetherionAI")

    $versionPath = Get-InternalPkiVersionFilePath -DeployPath $DeployPath
    if (-not (Test-Path $versionPath)) {
        return $false
    }

    $currentVersion = (Get-Content -Path $versionPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $currentVersion) {
        return $false
    }

    return ($currentVersion.Trim() -eq (Get-InternalPkiSchemaVersion))
}

function Test-InternalPkiFilesPresent {
    param([string]$DeployPath = "C:\ZetherionAI")

    if (-not (Test-InternalPkiSchemaCurrent -DeployPath $DeployPath)) {
        return $false
    }

    $defaults = Get-InternalPkiEnvDefaults -DeployPath $DeployPath
    $requiredPaths = @(
        $defaults["INTERNAL_TLS_CA_PATH"],
        $defaults["INTERNAL_TLS_CLIENT_CERT_PATH"],
        $defaults["INTERNAL_TLS_CLIENT_KEY_PATH"],
        $defaults["API_TLS_CERT_PATH"],
        $defaults["API_TLS_KEY_PATH"],
        $defaults["SKILLS_TLS_CERT_PATH"],
        $defaults["SKILLS_TLS_KEY_PATH"],
        $defaults["CGS_GATEWAY_TLS_CERT_PATH"],
        $defaults["CGS_GATEWAY_TLS_KEY_PATH"],
        $defaults["UPDATER_TLS_CERT_PATH"],
        $defaults["UPDATER_TLS_KEY_PATH"],
        $defaults["DEV_AGENT_API_TLS_CERT_PATH"],
        $defaults["DEV_AGENT_API_TLS_KEY_PATH"],
        $defaults["POSTGRES_TLS_CA_PATH"],
        $defaults["POSTGRES_TLS_CERT_PATH"],
        $defaults["POSTGRES_TLS_KEY_PATH"],
        $defaults["QDRANT_CERT_PATH"]
    )

    foreach ($path in $requiredPaths) {
        if (-not (Test-Path $path)) {
            return $false
        }
    }

    return $true
}

function Invoke-InternalPkiInitialization {
    param([string]$DeployPath = "C:\ZetherionAI")

    if (Test-InternalPkiFilesPresent -DeployPath $DeployPath) {
        return [pscustomobject]@{
            generated = $false
            certificate_root = (Join-Path $DeployPath "data\certs")
        }
    }

    $scriptPath = Join-Path $PSScriptRoot "initialize-internal-pki.ps1"
    if (-not (Test-Path $scriptPath)) {
        throw "Internal PKI initializer not found: $scriptPath"
    }

    $certificateRoot = Join-Path $DeployPath "data\certs"
    if ((Test-Path $certificateRoot) -and (-not (Test-InternalPkiSchemaCurrent -DeployPath $DeployPath))) {
        Remove-Item -Path $certificateRoot -Recurse -Force
    }

    $null = & $scriptPath -DeployPath $DeployPath | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "Internal PKI initialization failed for $DeployPath"
    }

    return [pscustomobject]@{
        generated = $true
        certificate_root = $certificateRoot
    }
}
