function Ensure-SecretParentDir {
    param([string]$Path)

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Initialize-ZetherionDpapiTypes {
    $ready = Get-Variable -Scope Script -Name ZetherionDpapiTypesReady -ValueOnly -ErrorAction SilentlyContinue
    if ($ready) {
        return
    }

    try {
        [void][System.Security.Cryptography.DataProtectionScope]::LocalMachine
        [void][System.Security.Cryptography.ProtectedData]
        $script:ZetherionDpapiTypesReady = $true
        return
    }
    catch {
        Add-Type -AssemblyName System.Security -ErrorAction SilentlyContinue
    }

    try {
        [void][System.Security.Cryptography.DataProtectionScope]::LocalMachine
        [void][System.Security.Cryptography.ProtectedData]
        $script:ZetherionDpapiTypesReady = $true
        return
    }
    catch {
        throw "DPAPI types are unavailable in this PowerShell session."
    }
}

function Resolve-RuntimeSecretBundlePath {
    param(
        [string]$DeployPath = "C:\ZetherionAI",
        [string]$SecretPath = ""
    )

    if ($SecretPath) {
        return $SecretPath
    }
    return (Join-Path $DeployPath "data\secrets\runtime.bin")
}

function Get-RuntimeSecretAllowlist {
    return @(
        "API_JWT_SECRET",
        "DISCORD_TOKEN",
        "DISCORD_BOT_TOKEN",
        "ENCRYPTION_PASSPHRASE",
        "ENCRYPTION_OWNER_PASSPHRASE",
        "ENCRYPTION_TENANT_PASSPHRASE",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "SKILLS_API_SECRET",
        "ZETHERION_SKILLS_API_SECRET",
        "WORKER_BRIDGE_BOOTSTRAP_SECRET",
        "OWNER_CI_WORKER_BOOTSTRAP_SECRET",
        "ANNOUNCEMENT_API_SECRET",
        "WHATSAPP_BRIDGE_SIGNING_SECRET",
        "WHATSAPP_BRIDGE_STATE_KEY",
        "CLOUDFLARE_TUNNEL_TOKEN",
        "CGS_BLOG_PUBLISH_TOKEN",
        "POSTGRES_PASSWORD",
        "TELEMETRY_API_KEY"
    )
}

function Resolve-SecretRunnerAccount {
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

function Set-SecretBundleAcl {
    param(
        [string]$Path,
        [string]$RunnerAccount
    )

    $acl = New-Object System.Security.AccessControl.FileSecurity
    $inheritanceFlags = [System.Security.AccessControl.InheritanceFlags]::None
    $propagationFlags = [System.Security.AccessControl.PropagationFlags]::None
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner([System.Security.Principal.NTAccount]"NT AUTHORITY\SYSTEM")

    foreach ($account in @("NT AUTHORITY\SYSTEM", "BUILTIN\Administrators")) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $account,
            "FullControl",
            $inheritanceFlags,
            $propagationFlags,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }

    if ($RunnerAccount) {
        $runnerRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $RunnerAccount,
            "Read",
            $inheritanceFlags,
            $propagationFlags,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($runnerRule)
    }

    Set-Acl -Path $Path -AclObject $acl
}

function Protect-MachineSecretPayload {
    param([string]$JsonText)

    Initialize-ZetherionDpapiTypes
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($JsonText)
    return [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
}

function Unprotect-MachineSecretPayload {
    param([byte[]]$CipherBytes)

    Initialize-ZetherionDpapiTypes
    return [System.Security.Cryptography.ProtectedData]::Unprotect(
        $CipherBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
}

function Get-EnvValueFromRuntimeEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if (-not (Test-Path $Path)) {
        return ""
    }

    foreach ($line in Get-Content -Path $Path) {
        if ($line -match "^\s*#") {
            continue
        }
        if ($line -notmatch "^\s*$([Regex]::Escape($Key))\s*=") {
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
        return $value
    }

    return ""
}

function Get-RuntimeSecretsFromEnvFile {
    param(
        [string]$EnvPath,
        [string[]]$Keys = $(Get-RuntimeSecretAllowlist)
    )

    $secrets = [ordered]@{}
    foreach ($key in $Keys) {
        $value = Get-EnvValueFromRuntimeEnvFile -Path $EnvPath -Key $key
        if ($value) {
            $secrets[$key] = $value
        }
    }
    return $secrets
}

function Write-RuntimeSecretsBundle {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Secrets,
        [string]$DeployPath = "C:\ZetherionAI",
        [string]$SecretPath = "",
        [string]$RunnerServiceAccount = ""
    )

    $resolvedSecretPath = Resolve-RuntimeSecretBundlePath -DeployPath $DeployPath -SecretPath $SecretPath
    Ensure-SecretParentDir -Path $resolvedSecretPath
    $runnerAccount = Resolve-SecretRunnerAccount -RequestedAccount $RunnerServiceAccount
    $payload = [ordered]@{
        version = 1
        generated_at = [DateTime]::UtcNow.ToString("o")
        secrets = $Secrets
    }
    $jsonText = $payload | ConvertTo-Json -Depth 8 -Compress
    $cipherBytes = Protect-MachineSecretPayload -JsonText $jsonText
    [System.IO.File]::WriteAllBytes($resolvedSecretPath, $cipherBytes)
    Set-SecretBundleAcl -Path $resolvedSecretPath -RunnerAccount $runnerAccount
    return $resolvedSecretPath
}

function Read-RuntimeSecretsBundle {
    param(
        [string]$DeployPath = "C:\ZetherionAI",
        [string]$SecretPath = ""
    )

    $resolvedSecretPath = Resolve-RuntimeSecretBundlePath -DeployPath $DeployPath -SecretPath $SecretPath
    if (-not (Test-Path $resolvedSecretPath)) {
        throw "Runtime secret bundle not found: $resolvedSecretPath"
    }

    $cipherBytes = [System.IO.File]::ReadAllBytes($resolvedSecretPath)
    if (-not $cipherBytes -or $cipherBytes.Length -eq 0) {
        throw "Runtime secret bundle is empty: $resolvedSecretPath"
    }

    $plainBytes = Unprotect-MachineSecretPayload -CipherBytes $cipherBytes
    $plainText = [System.Text.Encoding]::UTF8.GetString($plainBytes)
    if (-not $plainText) {
        throw "Runtime secret bundle decrypted to an empty payload."
    }

    $payload = $plainText | ConvertFrom-Json
    if (-not $payload.secrets) {
        throw "Runtime secret bundle payload missing 'secrets'."
    }
    return $payload.secrets
}

function Import-RuntimeSecretsBundle {
    param(
        [string]$DeployPath = "C:\ZetherionAI",
        [string]$SecretPath = "",
        [bool]$FailIfMissing = $false
    )

    $resolvedSecretPath = Resolve-RuntimeSecretBundlePath -DeployPath $DeployPath -SecretPath $SecretPath
    if (-not (Test-Path $resolvedSecretPath)) {
        if ($FailIfMissing) {
            throw "Runtime secret bundle not found: $resolvedSecretPath"
        }
        return [pscustomobject]@{
            found = $false
            imported_keys = @()
            secret_path = $resolvedSecretPath
        }
    }

    $secrets = Read-RuntimeSecretsBundle -DeployPath $DeployPath -SecretPath $resolvedSecretPath
    $importedKeys = New-Object 'System.Collections.Generic.List[string]'
    foreach ($property in $secrets.PSObject.Properties) {
        $name = [string]$property.Name
        $value = [string]$property.Value
        if (-not $name -or -not $value) {
            continue
        }
        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        $importedKeys.Add($name)
    }

    return [pscustomobject]@{
        found = $true
        imported_keys = [string[]]$importedKeys.ToArray()
        secret_path = $resolvedSecretPath
    }
}
