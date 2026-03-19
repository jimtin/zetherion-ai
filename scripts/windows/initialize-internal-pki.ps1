param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$ScriptPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ScriptPath) {
    $ScriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) "generate-internal-pki.sh"
}

if (-not (Test-Path $ScriptPath)) {
    throw "Internal PKI generator not found: $ScriptPath"
}

$certRoot = Join-Path $DeployPath "data\certs"

$wslScriptPath = & wsl.exe wslpath -a $ScriptPath
if ($LASTEXITCODE -ne 0 -or -not $wslScriptPath) {
    throw "Failed to translate PKI generator path for WSL execution."
}

$wslCertRoot = & wsl.exe wslpath -a $certRoot
if ($LASTEXITCODE -ne 0 -or -not $wslCertRoot) {
    throw "Failed to translate certificate output path for WSL execution."
}

& wsl.exe bash $wslScriptPath $wslCertRoot
if ($LASTEXITCODE -ne 0) {
    throw "Internal PKI generation failed."
}

[ordered]@{
    status = "success"
    deploy_path = $DeployPath
    certificate_root = $certRoot
    generator_path = $ScriptPath
} | ConvertTo-Json -Depth 4
