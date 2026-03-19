param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$ScriptPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-ZetherionWslPathForHost {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WindowsPath
    )

    $fullPath = [System.IO.Path]::GetFullPath($WindowsPath)
    $normalized = $fullPath -replace "\\", "/"
    $translated = (& wsl.exe wslpath -a -u $normalized | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $translated) {
        throw "Failed to translate Windows path for WSL execution: $WindowsPath"
    }

    return $translated
}

if (-not $ScriptPath) {
    $ScriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) "generate-internal-pki.sh"
}

if (-not (Test-Path $ScriptPath)) {
    throw "Internal PKI generator not found: $ScriptPath"
}

$certRoot = Join-Path $DeployPath "data\certs"

$wslScriptPath = ConvertTo-ZetherionWslPathForHost -WindowsPath $ScriptPath
$wslCertRoot = ConvertTo-ZetherionWslPathForHost -WindowsPath $certRoot

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
