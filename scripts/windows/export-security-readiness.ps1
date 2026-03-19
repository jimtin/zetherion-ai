param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionAI\data\security-readiness.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "runtime-secrets.ps1")

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Get-EnvValue {
    param([string]$Path, [string]$Key)
    return Get-EnvValueFromRuntimeEnvFile -Path $Path -Key $Key
}

function Test-HttpsUrl {
    param([string]$Value)
    if (-not $Value) {
        return $false
    }
    try {
        $uri = [Uri]$Value
        return $uri.Scheme -eq "https"
    }
    catch {
        return $false
    }
}

$envPath = Join-Path $DeployPath ".env"
$strictTransport = ([string](Get-EnvValue -Path $envPath -Key "STRICT_TRANSPORT_SECURITY")).Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
$runtimeSecretPath = Resolve-RuntimeSecretBundlePath -DeployPath $DeployPath
$transportInventory = @(
    [ordered]@{ name = "ANNOUNCEMENT_API_URL"; value = (Get-EnvValue -Path $envPath -Key "ANNOUNCEMENT_API_URL"); https = $false },
    [ordered]@{ name = "CGS_BLOG_PUBLISH_URL"; value = (Get-EnvValue -Path $envPath -Key "CGS_BLOG_PUBLISH_URL"); https = $false },
    [ordered]@{ name = "TELEMETRY_CENTRAL_URL"; value = (Get-EnvValue -Path $envPath -Key "TELEMETRY_CENTRAL_URL"); https = $false },
    [ordered]@{ name = "WHATSAPP_BRIDGE_INGEST_URL"; value = (Get-EnvValue -Path $envPath -Key "WHATSAPP_BRIDGE_INGEST_URL"); https = $false }
)

foreach ($entry in $transportInventory) {
    $entry.https = Test-HttpsUrl -Value ([string]$entry.value)
}

$certificateInventory = @(
    [ordered]@{ name = "internal_ca"; path = (Get-EnvValue -Path $envPath -Key "INTERNAL_TLS_CA_PATH") },
    [ordered]@{ name = "internal_client_cert"; path = (Get-EnvValue -Path $envPath -Key "INTERNAL_TLS_CLIENT_CERT_PATH") },
    [ordered]@{ name = "internal_client_key"; path = (Get-EnvValue -Path $envPath -Key "INTERNAL_TLS_CLIENT_KEY_PATH") },
    [ordered]@{ name = "qdrant_ca"; path = (Get-EnvValue -Path $envPath -Key "QDRANT_CERT_PATH") },
    [ordered]@{ name = "api_cert"; path = (Get-EnvValue -Path $envPath -Key "API_TLS_CERT_PATH") },
    [ordered]@{ name = "api_key"; path = (Get-EnvValue -Path $envPath -Key "API_TLS_KEY_PATH") },
    [ordered]@{ name = "skills_cert"; path = (Get-EnvValue -Path $envPath -Key "SKILLS_TLS_CERT_PATH") },
    [ordered]@{ name = "skills_key"; path = (Get-EnvValue -Path $envPath -Key "SKILLS_TLS_KEY_PATH") },
    [ordered]@{ name = "cgs_gateway_cert"; path = (Get-EnvValue -Path $envPath -Key "CGS_GATEWAY_TLS_CERT_PATH") },
    [ordered]@{ name = "cgs_gateway_key"; path = (Get-EnvValue -Path $envPath -Key "CGS_GATEWAY_TLS_KEY_PATH") },
    [ordered]@{ name = "updater_cert"; path = (Get-EnvValue -Path $envPath -Key "UPDATER_TLS_CERT_PATH") },
    [ordered]@{ name = "updater_key"; path = (Get-EnvValue -Path $envPath -Key "UPDATER_TLS_KEY_PATH") },
    [ordered]@{ name = "dev_agent_cert"; path = (Get-EnvValue -Path $envPath -Key "DEV_AGENT_API_TLS_CERT_PATH") },
    [ordered]@{ name = "dev_agent_key"; path = (Get-EnvValue -Path $envPath -Key "DEV_AGENT_API_TLS_KEY_PATH") },
    [ordered]@{ name = "postgres_ca"; path = (Get-EnvValue -Path $envPath -Key "POSTGRES_TLS_CA_PATH") },
    [ordered]@{ name = "postgres_client_cert"; path = (Get-EnvValue -Path $envPath -Key "POSTGRES_TLS_CERT_PATH") },
    [ordered]@{ name = "postgres_client_key"; path = (Get-EnvValue -Path $envPath -Key "POSTGRES_TLS_KEY_PATH") }
)

foreach ($entry in $certificateInventory) {
    $entry.exists = if ($entry.path) { [bool](Test-Path $entry.path) } else { $false }
}

$atRestInventory = [ordered]@{
    runtime_secret_bundle = [ordered]@{
        path = $runtimeSecretPath
        exists = [bool](Test-Path $runtimeSecretPath)
        classification = "application-encrypted"
    }
    postgres_data = [ordered]@{
        classification = "bitlocker-protected"
    }
    qdrant_storage = [ordered]@{
        classification = "bitlocker-protected"
        note = "Vector payloads rely on encrypted host storage rather than application-layer encryption."
    }
    windows_backup = [ordered]@{
        age_recipient_configured = [bool]([string](Get-EnvValue -Path $envPath -Key "BACKUP_AGE_RECIPIENT"))
        classification = "layered"
    }
}

try {
    $bitLockerVolumes = @(Get-BitLockerVolume | Where-Object { $_.MountPoint -match "^[A-Z]:$" })
    $atRestInventory.bitlocker = [ordered]@{
        volumes = @(
            $bitLockerVolumes | ForEach-Object {
                [ordered]@{
                    mount_point = $_.MountPoint
                    protection_status = [string]$_.ProtectionStatus
                }
            }
        )
    }
}
catch {
    $atRestInventory.bitlocker = [ordered]@{
        error = $_.Exception.Message
    }
}

$payload = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    deploy_path = $DeployPath
    strict_transport_security = $strictTransport
    http_free_policy = [ordered]@{
        enabled = $strictTransport
        violations = @(
            $transportInventory | Where-Object { $_.value -and -not $_.https } | ForEach-Object { $_.name }
        )
    }
    transport_inventory = $transportInventory
    certificate_inventory = $certificateInventory
    at_rest_inventory = $atRestInventory
}

Ensure-ParentDir -Path $OutputPath
$payload | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
$payload | ConvertTo-Json -Depth 10
