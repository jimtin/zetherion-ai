<#
.SYNOPSIS
    Verifies the Windows production host is correctly configured for Zetherion AI.

.DESCRIPTION
    Checks SSH server, Docker Desktop, network profile, firewall rules,
    container health, Ollama models, RBAC user, disk space, and Docker
    credential store. Outputs a structured JSON report.

.EXAMPLE
    # Run locally on the Windows machine:
    .\scripts\verify-windows-host.ps1

    # Run remotely via SSH from macOS:
    ssh -i ~/.ssh/zetherion_windows james@<WINDOWS_HOST_IP> "cd C:\ZetherionAI; powershell -File scripts\verify-windows-host.ps1"
#>

param(
    [string]$DeploymentPath = "C:\ZetherionAI"
)

$ErrorActionPreference = "Continue"

$report = @{
    timestamp   = (Get-Date -Format "o")
    hostname    = $env:COMPUTERNAME
    checks      = @{}
    overall     = "pass"
}

function Add-Check {
    param(
        [string]$Name,
        [string]$Status,  # pass, warn, fail
        [string]$Message
    )
    $report.checks[$Name] = @{
        status  = $Status
        message = $Message
    }
    if ($Status -eq "fail") {
        $report.overall = "fail"
    }
    elseif ($Status -eq "warn" -and $report.overall -ne "fail") {
        $report.overall = "warn"
    }
}

function Get-EnvValueFromFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if (-not (Test-Path $Path)) {
        return ""
    }

    $lines = Get-Content -Path $Path
    foreach ($line in $lines) {
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
        return $line.Substring($separatorIndex + 1).Trim()
    }

    return ""
}

function Test-Truthy {
    param([string]$Value)

    $normalized = [string]$Value
    $normalized = $normalized.Trim().ToLowerInvariant()
    return @("1", "true", "yes", "on") -contains $normalized
}

# 1. SSH Server
try {
    $sshService = Get-Service -Name sshd -ErrorAction Stop
    if ($sshService.Status -eq "Running" -and $sshService.StartType -eq "Automatic") {
        Add-Check -Name "ssh_server" -Status "pass" -Message "SSH server running, auto-start enabled"
    }
    elseif ($sshService.Status -eq "Running") {
        Add-Check -Name "ssh_server" -Status "warn" -Message "SSH running but StartType is $($sshService.StartType) (should be Automatic)"
    }
    else {
        Add-Check -Name "ssh_server" -Status "fail" -Message "SSH service status: $($sshService.Status)"
    }
}
catch {
    Add-Check -Name "ssh_server" -Status "fail" -Message "SSH server not installed"
}

# 2. Docker Desktop
try {
    $dockerInfo = docker info 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Add-Check -Name "docker_running" -Status "pass" -Message "Docker Desktop is running"
    }
    else {
        Add-Check -Name "docker_running" -Status "fail" -Message "Docker not responding"
    }
}
catch {
    Add-Check -Name "docker_running" -Status "fail" -Message "Docker command not found"
}

# Check Docker auto-start
try {
    $dockerAutoStart = Get-ItemProperty "HKCU:\Software\Docker Inc.\Docker Desktop" -Name "LaunchOnLogin" -ErrorAction Stop
    if ($dockerAutoStart.LaunchOnLogin -eq 1) {
        Add-Check -Name "docker_autostart" -Status "pass" -Message "Docker Desktop auto-start enabled"
    }
    else {
        Add-Check -Name "docker_autostart" -Status "warn" -Message "Docker Desktop auto-start disabled"
    }
}
catch {
    Add-Check -Name "docker_autostart" -Status "warn" -Message "Could not check Docker auto-start setting"
}

# 3. Network Profile
try {
    $profiles = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -ne "DomainAuthenticated" }
    $publicProfiles = $profiles | Where-Object { $_.NetworkCategory -eq "Public" }
    if ($publicProfiles.Count -eq 0) {
        Add-Check -Name "network_profile" -Status "pass" -Message "All network profiles are Private"
    }
    else {
        $names = ($publicProfiles | ForEach-Object { $_.Name }) -join ", "
        Add-Check -Name "network_profile" -Status "warn" -Message "Public profiles found: $names (SSH may be blocked)"
    }
}
catch {
    Add-Check -Name "network_profile" -Status "warn" -Message "Could not check network profiles"
}

# 4. Firewall Rule
try {
    $rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction Stop
    if ($rule.Enabled -eq "True") {
        Add-Check -Name "firewall_ssh" -Status "pass" -Message "SSH firewall rule exists and enabled"
    }
    else {
        Add-Check -Name "firewall_ssh" -Status "warn" -Message "SSH firewall rule exists but disabled"
    }
}
catch {
    # Try alternative names
    try {
        $rules = Get-NetFirewallRule | Where-Object { $_.DisplayName -like "*SSH*" -or $_.DisplayName -like "*OpenSSH*" }
        if ($rules) {
            Add-Check -Name "firewall_ssh" -Status "pass" -Message "SSH firewall rule found (alternative name)"
        }
        else {
            Add-Check -Name "firewall_ssh" -Status "warn" -Message "No SSH firewall rule found (may be using default Windows SSH rule)"
        }
    }
    catch {
        Add-Check -Name "firewall_ssh" -Status "warn" -Message "Could not check firewall rules"
    }
}

# 5. Container Health (blue/green topology)
$expectedContainers = @(
    "zetherion-ai-bot",
    "zetherion-ai-skills-blue",
    "zetherion-ai-skills-green",
    "zetherion-ai-api-blue",
    "zetherion-ai-api-green",
    "zetherion-ai-cgs-gateway-blue",
    "zetherion-ai-cgs-gateway-green",
    "zetherion-ai-ollama",
    "zetherion-ai-ollama-router",
    "zetherion-ai-postgres",
    "zetherion-ai-qdrant",
    "zetherion-ai-traefik",
    "zetherion-ai-updater",
    "zetherion-ai-dev-agent"
)

$envPath = Join-Path $DeploymentPath ".env"
$cloudflareToken = Get-EnvValueFromFile -Path $envPath -Key "CLOUDFLARE_TUNNEL_TOKEN"
if ($cloudflareToken) {
    $expectedContainers += "zetherion-ai-cloudflared"
}

$whatsappSigningSecret = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_SIGNING_SECRET"
$whatsappStateKey = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_STATE_KEY"
$whatsappTenantId = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_TENANT_ID"
$whatsappIngestUrl = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_INGEST_URL"
if ($whatsappSigningSecret -and $whatsappStateKey -and $whatsappTenantId -and $whatsappIngestUrl) {
    $expectedContainers += "zetherion-ai-whatsapp-bridge"
}

$allHealthy = $true
foreach ($container in $expectedContainers) {
    try {
        $status = docker inspect --format "{{.State.Status}}" $container 2>&1
        $health = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}" $container 2>&1
        if ($status -eq "running" -and ($health -eq "healthy" -or $health -eq "no-healthcheck")) {
            # pass
        }
        else {
            $allHealthy = $false
        }
    }
    catch {
        $allHealthy = $false
    }
}

if ($allHealthy) {
    Add-Check -Name "containers" -Status "pass" -Message "All expected runtime containers are running and healthy"
}
else {
    Add-Check -Name "containers" -Status "fail" -Message "Some containers are not running or unhealthy"
}

# 6. Ollama Models
try {
    $routerModels = docker exec zetherion-ai-ollama-router ollama list 2>&1 | Out-String
    $genModels = docker exec zetherion-ai-ollama ollama list 2>&1 | Out-String

    $hasRouter = $routerModels -match "llama3.2"
    $hasGen = $genModels -match "llama3.1"
    $hasEmbed = $genModels -match "nomic-embed"

    if ($hasRouter -and $hasGen -and $hasEmbed) {
        Add-Check -Name "ollama_models" -Status "pass" -Message "All required Ollama models present"
    }
    else {
        $missing = @()
        if (-not $hasRouter) { $missing += "llama3.2:3b (router)" }
        if (-not $hasGen) { $missing += "llama3.1:8b (generation)" }
        if (-not $hasEmbed) { $missing += "nomic-embed-text (embeddings)" }
        Add-Check -Name "ollama_models" -Status "fail" -Message "Missing models: $($missing -join ', ')"
    }
}
catch {
    Add-Check -Name "ollama_models" -Status "warn" -Message "Could not check Ollama models"
}

# 7. RBAC owner bootstrap determinism
try {
    $ownerUserId = Get-EnvValueFromFile -Path $envPath -Key "OWNER_USER_ID"
    $allowedUserIds = Get-EnvValueFromFile -Path $envPath -Key "ALLOWED_USER_IDS"
    $allowAllUsers = Test-Truthy -Value (Get-EnvValueFromFile -Path $envPath -Key "ALLOW_ALL_USERS")

    if ($allowAllUsers -and -not $ownerUserId -and -not $allowedUserIds) {
        Add-Check -Name "rbac_owner" -Status "fail" -Message "ALLOW_ALL_USERS=true with no OWNER_USER_ID/ALLOWED_USER_IDS (unsafe bootstrap)"
    }
    elseif ($ownerUserId) {
        if ($ownerUserId -notmatch "^\d+$") {
            Add-Check -Name "rbac_owner" -Status "fail" -Message "OWNER_USER_ID is configured but not numeric"
        }
        else {
            $ownerExistsRaw = docker exec zetherion-ai-postgres psql -U zetherion -d zetherion -t -c "SELECT COUNT(*) FROM users WHERE discord_user_id = $ownerUserId AND role = 'owner';" 2>&1 | Out-String
            $ownerExists = [int]($ownerExistsRaw.Trim())
            if ($ownerExists -gt 0) {
                Add-Check -Name "rbac_owner" -Status "pass" -Message "Configured OWNER_USER_ID exists with owner role in database"
            }
            else {
                Add-Check -Name "rbac_owner" -Status "fail" -Message "Configured OWNER_USER_ID is not present with owner role in database"
            }
        }
    }
    else {
        $ownerCountRaw = docker exec zetherion-ai-postgres psql -U zetherion -d zetherion -t -c "SELECT COUNT(*) FROM users WHERE role = 'owner';" 2>&1 | Out-String
        $ownerCount = [int]($ownerCountRaw.Trim())
        if ($ownerCount -gt 0) {
            Add-Check -Name "rbac_owner" -Status "warn" -Message "Owner exists in database but OWNER_USER_ID is not set in .env"
        }
        else {
            Add-Check -Name "rbac_owner" -Status "fail" -Message "No owner user in database and OWNER_USER_ID is not configured"
        }
    }
}
catch {
    Add-Check -Name "rbac_owner" -Status "warn" -Message "Could not check RBAC users"
}

# 8. Disk Space
try {
    $drive = Get-PSDrive C
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -gt 20) {
        Add-Check -Name "disk_space" -Status "pass" -Message "${freeGB}GB free on C:"
    }
    elseif ($freeGB -gt 10) {
        Add-Check -Name "disk_space" -Status "warn" -Message "${freeGB}GB free on C: (getting low)"
    }
    else {
        Add-Check -Name "disk_space" -Status "fail" -Message "${freeGB}GB free on C: (critically low)"
    }
}
catch {
    Add-Check -Name "disk_space" -Status "warn" -Message "Could not check disk space"
}

# 9. Docker Credential Store
try {
    $dockerConfig = Get-Content "$env:USERPROFILE\.docker\config.json" | ConvertFrom-Json
    if ($dockerConfig.credsStore -eq "desktop") {
        Add-Check -Name "docker_credstore" -Status "warn" -Message "credsStore=desktop (will fail for SSH-based operations — disable before remote deploys)"
    }
    elseif ([string]::IsNullOrEmpty($dockerConfig.credsStore)) {
        Add-Check -Name "docker_credstore" -Status "pass" -Message "credsStore disabled (SSH-compatible)"
    }
    else {
        Add-Check -Name "docker_credstore" -Status "pass" -Message "credsStore=$($dockerConfig.credsStore)"
    }
}
catch {
    Add-Check -Name "docker_credstore" -Status "warn" -Message "Could not check Docker credential store"
}

# Output report as JSON
$report | ConvertTo-Json -Depth 4
