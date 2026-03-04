param(
    [Parameter(Mandatory = $false)]
    [string]$SecretPath = "C:\ZetherionAI\data\secrets\promotions.bin"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Decode-SecretsPayload {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Promotions secret blob not found: $Path"
    }

    $cipherBytes = [System.IO.File]::ReadAllBytes($Path)
    if (-not $cipherBytes -or $cipherBytes.Length -eq 0) {
        throw "Promotions secret blob is empty: $Path"
    }

    try {
        $plainBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $cipherBytes,
            $null,
            [System.Security.Cryptography.DataProtectionScope]::LocalMachine
        )
    }
    catch {
        throw "Unable to decrypt promotions secret blob with DPAPI LocalMachine."
    }

    $raw = [System.Text.Encoding]::UTF8.GetString($plainBytes)
    if (-not $raw) {
        throw "Decrypted promotions secret payload is empty."
    }

    try {
        return $raw | ConvertFrom-Json
    }
    catch {
        throw "Decrypted promotions secret payload is not valid JSON."
    }
}

function Require-Secret {
    param([object]$Secrets, [string]$Name)
    $value = [string]($Secrets.$Name)
    if (-not $value) {
        throw "Missing required secret: $Name"
    }
    return $value
}

$payload = Decode-SecretsPayload -Path $SecretPath
if (-not $payload.secrets) {
    throw "Secret payload missing 'secrets' object."
}

$secrets = $payload.secrets

Require-Secret -Secrets $secrets -Name "CGS_BLOG_PUBLISH_URL" | Out-Null
Require-Secret -Secrets $secrets -Name "CGS_BLOG_PUBLISH_TOKEN" | Out-Null
Require-Secret -Secrets $secrets -Name "OPENAI_API_KEY" | Out-Null
Require-Secret -Secrets $secrets -Name "ANTHROPIC_API_KEY" | Out-Null
Require-Secret -Secrets $secrets -Name "GITHUB_PROMOTION_TOKEN" | Out-Null
Require-Secret -Secrets $secrets -Name "DISCORD_DM_NOTIFY_ENABLED" | Out-Null

$primaryModel = Require-Secret -Secrets $secrets -Name "BLOG_MODEL_PRIMARY"
$secondaryModel = Require-Secret -Secrets $secrets -Name "BLOG_MODEL_SECONDARY"
$discordDmEnabledRaw = [string]($secrets.DISCORD_DM_NOTIFY_ENABLED)
$discordDmEnabled = $discordDmEnabledRaw.ToLowerInvariant() -in @("1", "true", "yes", "on")

$discordBotToken = [string]($secrets.DISCORD_BOT_TOKEN)
$discordNotifyUserId = [string]($secrets.DISCORD_NOTIFY_USER_ID)
$ownerUserId = [string]($secrets.OWNER_USER_ID)

if ($discordDmEnabled) {
    if (-not $discordBotToken) {
        throw "DISCORD_BOT_TOKEN is required when DISCORD_DM_NOTIFY_ENABLED=true."
    }
    if (-not $discordNotifyUserId -and -not $ownerUserId) {
        throw "Either DISCORD_NOTIFY_USER_ID or OWNER_USER_ID is required when DISCORD_DM_NOTIFY_ENABLED=true."
    }
}

if ($primaryModel -ne "gpt-5.2") {
    throw "BLOG_MODEL_PRIMARY must be 'gpt-5.2'."
}
if ($secondaryModel -ne "claude-sonnet-4-6") {
    throw "BLOG_MODEL_SECONDARY must be 'claude-sonnet-4-6'."
}

$result = [ordered]@{
    status = "success"
    secret_path = $SecretPath
    generated_at = [DateTime]::UtcNow.ToString("o")
    required_keys_present = $true
    model_primary = $primaryModel
    model_secondary = $secondaryModel
    blog_publish_enabled = [string]($secrets.BLOG_PUBLISH_ENABLED ?? "true")
    release_auto_increment_enabled = [string]($secrets.RELEASE_AUTO_INCREMENT_ENABLED ?? "true")
    discord_dm_notify_enabled = $discordDmEnabledRaw
    discord_notify_user_id_present = [bool]$discordNotifyUserId
    owner_user_id_present = [bool]$ownerUserId
}

$result | ConvertTo-Json -Depth 8
