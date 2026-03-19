param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$EnvPath = "",
    [Parameter(Mandatory = $false)]
    [string]$SecretPath = "",
    [Parameter(Mandatory = $false)]
    [string]$RunnerServiceAccount = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "runtime-secrets.ps1")

if (-not $EnvPath) {
    $EnvPath = Join-Path $DeployPath ".env"
}

$secrets = Get-RuntimeSecretsFromEnvFile -EnvPath $EnvPath
if ($secrets.Count -eq 0) {
    throw "No runtime secrets were found in $EnvPath for the runtime secret allowlist."
}

$resolvedSecretPath = Write-RuntimeSecretsBundle `
    -Secrets $secrets `
    -DeployPath $DeployPath `
    -SecretPath $SecretPath `
    -RunnerServiceAccount $RunnerServiceAccount

[ordered]@{
    status = "success"
    deploy_path = $DeployPath
    env_path = $EnvPath
    secret_path = $resolvedSecretPath
    imported_secret_count = $secrets.Count
    imported_secret_keys = @($secrets.Keys | Sort-Object)
} | ConvertTo-Json -Depth 8
