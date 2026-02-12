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

# 5. Container Health
$expectedContainers = @(
    "zetherion-ai-bot",
    "zetherion-ai-skills",
    "zetherion-ai-api",
    "zetherion-ai-ollama",
    "zetherion-ai-ollama-router",
    "zetherion-ai-postgres",
    "zetherion-ai-qdrant",
    "zetherion-ai-traefik",
    "zetherion-ai-cloudflared",
    "zetherion-ai-updater"
)

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
    Add-Check -Name "containers" -Status "pass" -Message "All expected containers are running and healthy"
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

# 7. RBAC Owner User
try {
    $userCheck = docker exec zetherion-ai-postgres psql -U zetherion -d zetherion -t -c "SELECT COUNT(*) FROM users WHERE role = 'owner';" 2>&1 | Out-String
    $ownerCount = [int]($userCheck.Trim())
    if ($ownerCount -gt 0) {
        Add-Check -Name "rbac_owner" -Status "pass" -Message "Owner user exists in database ($ownerCount found)"
    }
    else {
        Add-Check -Name "rbac_owner" -Status "fail" -Message "No owner user in database — bot will reject all messages"
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
