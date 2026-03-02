param(
    [Parameter(Mandatory = $false)]
    [string]$SecretPath = "C:\ZetherionAI\data\secrets\promotions.bin",
    [Parameter(Mandatory = $true)]
    [string]$CgsBlogPublishUrl,
    [Parameter(Mandatory = $true)]
    [string]$CgsBlogPublishToken,
    [Parameter(Mandatory = $true)]
    [string]$OpenAiApiKey,
    [Parameter(Mandatory = $true)]
    [string]$AnthropicApiKey,
    [Parameter(Mandatory = $true)]
    [string]$GitHubPromotionToken,
    [Parameter(Mandatory = $false)]
    [string]$GitHubRepository = "",
    [Parameter(Mandatory = $false)]
    [string]$BlogModelPrimary = "gpt-5.2",
    [Parameter(Mandatory = $false)]
    [string]$BlogModelSecondary = "claude-sonnet-4-6",
    [Parameter(Mandatory = $false)]
    [string]$BlogPublishEnabled = "true",
    [Parameter(Mandatory = $false)]
    [string]$ReleaseAutoIncrementEnabled = "true",
    [Parameter(Mandatory = $false)]
    [string]$RunnerServiceAccount = "NT AUTHORITY\NETWORK SERVICE"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($BlogModelPrimary -ne "gpt-5.2") {
    throw "BlogModelPrimary must be 'gpt-5.2'."
}
if ($BlogModelSecondary -ne "claude-sonnet-4-6") {
    throw "BlogModelSecondary must be 'claude-sonnet-4-6'."
}

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function New-SecureBlob {
    param([string]$JsonText)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($JsonText)
    return [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
}

function Set-SecretFileAcl {
    param(
        [string]$Path,
        [string]$RunnerAccount
    )

    $acl = New-Object System.Security.AccessControl.FileSecurity
    $inheritanceFlags = [System.Security.AccessControl.InheritanceFlags]::None
    $propagationFlags = [System.Security.AccessControl.PropagationFlags]::None

    $systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "NT AUTHORITY\SYSTEM",
        "FullControl",
        $inheritanceFlags,
        $propagationFlags,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "BUILTIN\Administrators",
        "FullControl",
        $inheritanceFlags,
        $propagationFlags,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $runnerRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $RunnerAccount,
        "Read",
        $inheritanceFlags,
        $propagationFlags,
        [System.Security.AccessControl.AccessControlType]::Allow
    )

    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner([System.Security.Principal.NTAccount]"NT AUTHORITY\SYSTEM")
    $acl.AddAccessRule($systemRule)
    $acl.AddAccessRule($adminRule)
    $acl.AddAccessRule($runnerRule)
    Set-Acl -Path $Path -AclObject $acl
}

Ensure-ParentDir -Path $SecretPath

$payload = [ordered]@{
    version = 1
    generated_at = [DateTime]::UtcNow.ToString("o")
    secrets = [ordered]@{
        CGS_BLOG_PUBLISH_URL = $CgsBlogPublishUrl
        CGS_BLOG_PUBLISH_TOKEN = $CgsBlogPublishToken
        OPENAI_API_KEY = $OpenAiApiKey
        ANTHROPIC_API_KEY = $AnthropicApiKey
        GITHUB_PROMOTION_TOKEN = $GitHubPromotionToken
        GITHUB_REPOSITORY = $GitHubRepository
        BLOG_MODEL_PRIMARY = $BlogModelPrimary
        BLOG_MODEL_SECONDARY = $BlogModelSecondary
        BLOG_PUBLISH_ENABLED = $BlogPublishEnabled
        RELEASE_AUTO_INCREMENT_ENABLED = $ReleaseAutoIncrementEnabled
    }
}

$payloadJson = $payload | ConvertTo-Json -Depth 8 -Compress
$cipherBytes = New-SecureBlob -JsonText $payloadJson
[System.IO.File]::WriteAllBytes($SecretPath, $cipherBytes)
Set-SecretFileAcl -Path $SecretPath -RunnerAccount $RunnerServiceAccount

$result = [ordered]@{
    status = "success"
    secret_path = $SecretPath
    generated_at = [DateTime]::UtcNow.ToString("o")
    model_primary = $BlogModelPrimary
    model_secondary = $BlogModelSecondary
    blog_publish_enabled = $BlogPublishEnabled
    release_auto_increment_enabled = $ReleaseAutoIncrementEnabled
}

$result | ConvertTo-Json -Depth 8
