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
    [string]$AnnouncementEmitEnabled = "",
    [Parameter(Mandatory = $false)]
    [string]$AnnouncementApiUrl = "https://127.0.0.1:8080/announcements/events",
    [Parameter(Mandatory = $false)]
    [string]$AnnouncementApiSecret = "",
    [Parameter(Mandatory = $false)]
    [string]$AnnouncementTargetUserId = "",
    [Parameter(Mandatory = $false)]
    [string]$DiscordDmNotifyEnabled = "false",
    [Parameter(Mandatory = $false)]
    [string]$DiscordBotToken = "",
    [Parameter(Mandatory = $false)]
    [string]$DiscordNotifyUserId = "",
    [Parameter(Mandatory = $false)]
    [string]$OwnerUserId = "",
    [Parameter(Mandatory = $false)]
    [string]$RunnerServiceAccount = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($BlogModelPrimary -ne "gpt-5.2") {
    throw "BlogModelPrimary must be 'gpt-5.2'."
}
if ($BlogModelSecondary -ne "claude-sonnet-4-6") {
    throw "BlogModelSecondary must be 'claude-sonnet-4-6'."
}

$announcementEnabledRaw = if ($AnnouncementEmitEnabled) { $AnnouncementEmitEnabled } else { $DiscordDmNotifyEnabled }
$announcementEnabled = $announcementEnabledRaw.ToLowerInvariant() -in @("1", "true", "yes", "on")
$effectiveAnnouncementTargetUserId = if ($AnnouncementTargetUserId) { $AnnouncementTargetUserId } elseif ($DiscordNotifyUserId) { $DiscordNotifyUserId } else { "" }

if ($announcementEnabled) {
    if (-not $AnnouncementApiSecret) {
        throw "AnnouncementApiSecret is required when announcement emit is enabled."
    }
    if (-not $effectiveAnnouncementTargetUserId -and -not $OwnerUserId) {
        throw "Either AnnouncementTargetUserId/DiscordNotifyUserId or OwnerUserId is required when announcement emit is enabled."
    }
}

$dmEnabled = $DiscordDmNotifyEnabled.ToLowerInvariant() -in @("1", "true", "yes", "on")
if ($dmEnabled) {
    if (-not $DiscordBotToken) {
        throw "DiscordBotToken is required when DiscordDmNotifyEnabled=true."
    }
    if (-not $DiscordNotifyUserId -and -not $OwnerUserId) {
        throw "Either DiscordNotifyUserId or OwnerUserId is required when DiscordDmNotifyEnabled=true."
    }
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

function Resolve-RunnerAccount {
    param([string]$RequestedAccount)

    if ($RequestedAccount) {
        return $RequestedAccount
    }

    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        if ($identity -and $identity.Name) {
            return [string]$identity.Name
        }
    } catch {
        # Ignore and fall back to environment-derived actor.
    }

    if ($env:USERDOMAIN -and $env:USERNAME) {
        return "$env:USERDOMAIN\$env:USERNAME"
    }

    if ($env:USERNAME) {
        return [string]$env:USERNAME
    }

    throw "RunnerServiceAccount must resolve to a Windows user principal."
}

Ensure-ParentDir -Path $SecretPath
$RunnerServiceAccount = Resolve-RunnerAccount -RequestedAccount $RunnerServiceAccount

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
        ANNOUNCEMENT_EMIT_ENABLED = $announcementEnabledRaw
        ANNOUNCEMENT_API_URL = $AnnouncementApiUrl
        ANNOUNCEMENT_API_SECRET = $AnnouncementApiSecret
        ANNOUNCEMENT_TARGET_USER_ID = $effectiveAnnouncementTargetUserId
        DISCORD_DM_NOTIFY_ENABLED = $DiscordDmNotifyEnabled
        DISCORD_BOT_TOKEN = $DiscordBotToken
        DISCORD_NOTIFY_USER_ID = $DiscordNotifyUserId
        OWNER_USER_ID = $OwnerUserId
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
    announcement_emit_enabled = $announcementEnabledRaw
    announcement_api_url = $AnnouncementApiUrl
    announcement_target_user_id = $effectiveAnnouncementTargetUserId
    discord_dm_notify_enabled = $DiscordDmNotifyEnabled
}

$result | ConvertTo-Json -Depth 8
