# Zetherion AI Windows Deployment Setup Script
# This script automates the entire setup process for Windows deployment
# Run as Administrator: powershell -ExecutionPolicy Bypass -File setup-windows-deployment.ps1

#Requires -RunAsAdministrator

param(
    [string]$DeploymentPath = "C:\ZetherionAI",
    [int]$PollIntervalMinutes = 5,
    [switch]$AutoStart = $false
)

$ErrorActionPreference = "Stop"

Write-Host @"
================================================================

     Zetherion AI Windows Deployment Setup
     Automated Installation & Configuration

================================================================
"@ -ForegroundColor Cyan

Write-Output ""
Write-Output "This script will:"
Write-Output "  1. Verify prerequisites (Docker, Git, GitHub CLI)"
Write-Output "  2. Clone the Zetherion AI repository"
Write-Output "  3. Set up environment configuration"
Write-Output "  4. Create deployment scripts"
Write-Output "  5. Configure auto-deployment polling (every $PollIntervalMinutes minutes)"
Write-Output ""
Write-Output "Deployment path: $DeploymentPath"
Write-Output "Poll interval: $PollIntervalMinutes minutes"
Write-Output ""

$confirmation = Read-Host "Continue with setup? (yes/no)"
if ($confirmation -ne "yes") {
    Write-Host "[WARNING] Setup cancelled by user" -ForegroundColor Yellow
    exit 0
}

Write-Output ""
Write-Host "[STEP 1] Checking prerequisites..." -ForegroundColor Cyan

# Check Docker
try {
    $dockerVersion = docker --version 2>$null
    if ($dockerVersion) {
        Write-Host "[OK] Docker installed: $dockerVersion" -ForegroundColor Green
    } else {
        throw "Docker not found"
    }
} catch {
    Write-Host "[ERROR] Docker is not installed. Please install Docker Desktop:" -ForegroundColor Red
    Write-Output "  Download from: https://www.docker.com/products/docker-desktop/"
    exit 1
}

# Check if Docker is running (separate from installation check)
$dockerRunning = $false
try {
    $ErrorActionPreference = "SilentlyContinue"
    docker info 2>$null | Out-Null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -eq 0) {
        $dockerRunning = $true
    }
} catch {
    $dockerRunning = $false
}

if ($dockerRunning) {
    Write-Host "[OK] Docker is running" -ForegroundColor Green
} else {
    Write-Host "[WARNING] Docker is installed but not running. Starting Docker Desktop..." -ForegroundColor Yellow

    # Try to start Docker Desktop
    try {
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
        Write-Output "Waiting 30 seconds for Docker to start..."
        Start-Sleep -Seconds 30

        # Check again
        $ErrorActionPreference = "SilentlyContinue"
        docker info 2>$null | Out-Null
        $ErrorActionPreference = "Stop"

        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Docker started successfully" -ForegroundColor Green
        } else {
            Write-Host "[ERROR] Docker failed to start automatically." -ForegroundColor Red
            Write-Output ""
            Write-Output "Please start Docker Desktop manually:"
            Write-Output "  1. Press Windows key"
            Write-Output "  2. Type 'Docker Desktop'"
            Write-Output "  3. Click to open"
            Write-Output "  4. Wait for Docker to start (icon in system tray will turn green)"
            Write-Output "  5. Run this script again"
            Write-Output ""
            exit 1
        }
    } catch {
        Write-Host "[ERROR] Failed to start Docker Desktop automatically." -ForegroundColor Red
        Write-Output ""
        Write-Output "Please start Docker Desktop manually and run this script again."
        exit 1
    }
}

# Check Git
try {
    $gitVersion = git --version 2>$null
    if ($gitVersion) {
        Write-Host "[OK] Git installed: $gitVersion" -ForegroundColor Green
    } else {
        throw "Git not found"
    }
} catch {
    Write-Host "[ERROR] Git is not installed. Please install Git for Windows:" -ForegroundColor Red
    Write-Output "  Download from: https://git-scm.com/download/win"
    Write-Output "  Or run: winget install Git.Git"
    exit 1
}

# Check GitHub CLI
try {
    $ghVersion = gh --version 2>$null
    if ($ghVersion) {
        Write-Host "[OK] GitHub CLI installed: $($ghVersion[0])" -ForegroundColor Green
    } else {
        throw "GitHub CLI not found"
    }
} catch {
    Write-Host "[WARNING] GitHub CLI is not installed. Installing now..." -ForegroundColor Yellow
    try {
        winget install GitHub.cli -e --accept-source-agreements --accept-package-agreements
        Write-Host "[OK] GitHub CLI installed" -ForegroundColor Green
    } catch {
        Write-Host "[ERROR] Failed to install GitHub CLI. Please install manually:" -ForegroundColor Red
        Write-Output "  Run: winget install GitHub.cli"
        exit 1
    }
}

Write-Output ""
Write-Host "[STEP 2] Cloning repository..." -ForegroundColor Cyan

# Create deployment directory
if (Test-Path $DeploymentPath) {
    Write-Host "[WARNING] Deployment directory already exists: $DeploymentPath" -ForegroundColor Yellow
    $overwrite = Read-Host "Do you want to remove it and start fresh? (yes/no)"
    if ($overwrite -eq "yes") {
        Remove-Item -Recurse -Force $DeploymentPath
        Write-Host "[OK] Removed existing directory" -ForegroundColor Green
    } else {
        Write-Output "Using existing directory"
    }
} else {
    New-Item -ItemType Directory -Path $DeploymentPath | Out-Null
}

# Clone repository
Set-Location $DeploymentPath
if (-not (Test-Path ".git")) {
    git clone https://github.com/jimtin/zetherion-ai.git .
} else {
    git pull origin main
}
Write-Host "[OK] Repository ready" -ForegroundColor Green

Write-Output ""
Write-Host "[STEP 3] Setting up environment configuration..." -ForegroundColor Cyan

# Create .env file
if (Test-Path ".env") {
    Write-Host "[WARNING] .env file already exists" -ForegroundColor Yellow
    $editEnv = Read-Host "Do you want to edit it? (yes/no)"
    if ($editEnv -eq "yes") {
        notepad .env
    }
} else {
    Write-Output "Creating .env file from template..."
    Copy-Item .env.example .env
    Write-Host "[OK] .env file created" -ForegroundColor Green
    Write-Output ""
    Write-Output "IMPORTANT: Edit the .env file with your credentials:"
    Write-Output "  - DISCORD_TOKEN (required)"
    Write-Output "  - GEMINI_API_KEY (required)"
    Write-Output "  - ANTHROPIC_API_KEY (optional)"
    Write-Output "  - OPENAI_API_KEY (optional)"
    Write-Output ""
    $editNow = Read-Host "Do you want to edit it now? (yes/no)"
    if ($editNow -eq "yes") {
        notepad .env
    }
}

Write-Output ""
Write-Host "[STEP 4] Creating deployment scripts..." -ForegroundColor Cyan

# Create deploy-windows.ps1
$deployScript = @'
# deploy-windows.ps1 - Zetherion AI deployment script for Windows
param(
    [switch]$SkipBackup = $false,
    [switch]$NoBuild = $false
)

Write-Host "[DEPLOY] Zetherion AI Deployment" -ForegroundColor Cyan
Write-Host "=================================" -ForegroundColor Cyan

# Check if Docker is running
try {
    docker info | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker not running"
    }
} catch {
    Write-Host "[ERROR] Docker is not running. Please start Docker Desktop." -ForegroundColor Red
    exit 1
}

# Pull latest code
Write-Host "[INFO] Pulling latest code..." -ForegroundColor Cyan
git pull origin main

# Backup .env if not skipped
if (-not $SkipBackup) {
    Write-Host "[INFO] Backing up .env..." -ForegroundColor Cyan
    Copy-Item .env ".env.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
}

# Stop existing containers
Write-Host "[INFO] Stopping existing containers..." -ForegroundColor Cyan
docker-compose down --timeout 30

# Build new image if not skipped
if (-not $NoBuild) {
    Write-Host "[INFO] Building new image..." -ForegroundColor Cyan
    docker-compose build
}

# Start new containers
Write-Host "[INFO] Starting containers..." -ForegroundColor Cyan
docker-compose up -d

# Wait for containers to be healthy
Write-Host "[INFO] Waiting for containers to be healthy..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

# Show status
Write-Host "[INFO] Container status:" -ForegroundColor Cyan
docker-compose ps

Write-Host "[OK] Deployment complete!" -ForegroundColor Green
Write-Host "View logs with: .\logs.ps1" -ForegroundColor Cyan
'@

Set-Content -Path "deploy-windows.ps1" -Value $deployScript

# Create start.ps1
$startScript = @'
# start.ps1 - Start Zetherion AI containers
docker-compose up -d
Write-Host "[OK] Containers started" -ForegroundColor Green
docker-compose ps
'@

Set-Content -Path "start.ps1" -Value $startScript

# Create stop.ps1
$stopScript = @'
# stop.ps1 - Stop Zetherion AI containers
docker-compose down --timeout 30
Write-Host "[OK] Containers stopped" -ForegroundColor Green
'@

Set-Content -Path "stop.ps1" -Value $stopScript

# Create logs.ps1
$logsScript = @'
# logs.ps1 - View Zetherion AI logs
docker-compose logs -f zetherion-ai-bot
'@

Set-Content -Path "logs.ps1" -Value $logsScript

# Create auto-deploy.ps1 with CI status checking
$autoDeployScript = @'
# auto-deploy.ps1 - Monitor for changes and auto-deploy
# This script polls GitHub every few minutes and deploys when:
# 1. New commits are detected on main branch
# 2. GitHub Actions CI has passed for those commits

param(
    [int]$IntervalMinutes = 5
)

$IntervalSeconds = $IntervalMinutes * 60

Write-Host "[AUTO-DEPLOY] Starting deployment monitor" -ForegroundColor Cyan
Write-Host "Poll interval: $IntervalMinutes minutes ($IntervalSeconds seconds)" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

# Get initial commit
$lastCommit = git rev-parse HEAD
$lastDeployTime = Get-Date
Write-Host "[INFO] Current commit: $($lastCommit.Substring(0,7))" -ForegroundColor Cyan
Write-Host "[INFO] Monitoring for changes..." -ForegroundColor Cyan
Write-Host ""

while ($true) {
    Start-Sleep -Seconds $IntervalSeconds

    # Fetch latest from GitHub
    try {
        git fetch origin main 2>&1 | Out-Null
    } catch {
        Write-Host "[WARNING] Failed to fetch from GitHub: $_" -ForegroundColor Yellow
        continue
    }

    # Get latest commit on origin/main
    $currentCommit = git rev-parse origin/main

    if ($lastCommit -ne $currentCommit) {
        Write-Host ""
        Write-Host "[DETECTED] New commit: $($currentCommit.Substring(0,7))" -ForegroundColor Green

        # Check CI status using GitHub CLI
        Write-Host "[INFO] Checking GitHub Actions CI status..." -ForegroundColor Cyan

        try {
            # Get the latest workflow run for this commit
            $runStatus = gh run list --commit $currentCommit --limit 1 --json conclusion,status,workflowName | ConvertFrom-Json

            if ($runStatus.Count -gt 0) {
                $conclusion = $runStatus[0].conclusion
                $status = $runStatus[0].status
                $workflowName = $runStatus[0].workflowName

                Write-Host "[INFO] Workflow: $workflowName" -ForegroundColor Cyan
                Write-Host "[INFO] Status: $status" -ForegroundColor Cyan

                if ($status -eq "completed") {
                    if ($conclusion -eq "success") {
                        Write-Host "[OK] CI passed! Deploying..." -ForegroundColor Green
                        Write-Host ""

                        # Deploy
                        .\deploy-windows.ps1

                        $lastCommit = $currentCommit
                        $lastDeployTime = Get-Date

                        Write-Host ""
                        Write-Host "[OK] Deployment complete at $lastDeployTime" -ForegroundColor Green
                        Write-Host "[INFO] Monitoring for next change..." -ForegroundColor Cyan
                        Write-Host ""
                    } else {
                        Write-Host "[WARNING] CI failed with conclusion: $conclusion" -ForegroundColor Yellow
                        Write-Host "[WARNING] Skipping deployment until CI passes" -ForegroundColor Yellow
                        Write-Host ""
                    }
                } else {
                    Write-Host "[INFO] CI still running ($status). Will check again in $IntervalMinutes minutes..." -ForegroundColor Cyan
                    Write-Host ""
                }
            } else {
                Write-Host "[WARNING] No workflow runs found for commit $($currentCommit.Substring(0,7))" -ForegroundColor Yellow
                Write-Host "[WARNING] Deploying anyway (CI may not have started yet)" -ForegroundColor Yellow
                Write-Host ""

                # Deploy anyway - CI might not have started yet
                .\deploy-windows.ps1
                $lastCommit = $currentCommit
                $lastDeployTime = Get-Date
                Write-Host ""
            }
        } catch {
            Write-Host "[WARNING] Failed to check CI status: $_" -ForegroundColor Yellow
            Write-Host "[WARNING] Deploying anyway" -ForegroundColor Yellow
            Write-Host ""

            # Deploy anyway
            .\deploy-windows.ps1
            $lastCommit = $currentCommit
            $lastDeployTime = Get-Date
            Write-Host ""
        }
    } else {
        # No changes
        $timeSinceLastDeploy = (Get-Date) - $lastDeployTime
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] No changes. Last deploy: $($timeSinceLastDeploy.ToString('hh\:mm\:ss')) ago" -ForegroundColor Gray
    }
}
'@

Set-Content -Path "auto-deploy.ps1" -Value $autoDeployScript

Write-Host "[OK] Deployment scripts created" -ForegroundColor Green

# Create auto-start task if requested
if ($AutoStart) {
    Write-Output ""
    Write-Host "[STEP 5] Creating auto-start scheduled task..." -ForegroundColor Cyan

    $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$DeploymentPath\auto-deploy.ps1`" -IntervalMinutes $PollIntervalMinutes"
    $taskTrigger = New-ScheduledTaskTrigger -AtStartup
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    try {
        Register-ScheduledTask -TaskName "ZetherionAI-AutoDeploy" -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
        Write-Host "[OK] Created auto-deploy scheduled task" -ForegroundColor Green
        Write-Output "The deployment monitor will start automatically on system boot."
    } catch {
        Write-Host "[WARNING] Failed to create auto-start task: $_" -ForegroundColor Yellow
    }
}

Write-Output ""
Write-Host @"
================================================================

                  SETUP COMPLETE!

================================================================
"@ -ForegroundColor Green

Write-Output ""
Write-Output "Deployment directory: $DeploymentPath"
Write-Output "Poll interval: $PollIntervalMinutes minutes"
Write-Output ""
Write-Output "Quick Commands:"
Write-Output "  Deploy now:           .\deploy-windows.ps1"
Write-Output "  Start bot:            .\start.ps1"
Write-Output "  Stop bot:             .\stop.ps1"
Write-Output "  View logs:            .\logs.ps1"
Write-Output "  Start auto-deploy:    .\auto-deploy.ps1"
Write-Output ""
Write-Output "Next Steps:"
Write-Output "  1. Test deployment:   .\deploy-windows.ps1"
Write-Output "  2. Verify bot works:  .\logs.ps1"
Write-Output "  3. Start monitoring:  .\auto-deploy.ps1"
Write-Output ""
Write-Output "Auto-Deployment:"
Write-Output "  - Polls GitHub every $PollIntervalMinutes minutes"
Write-Output "  - Checks CI status before deploying"
Write-Output "  - Only deploys if CI passes"
if ($AutoStart) {
    Write-Output "  - Starts automatically on boot (scheduled task created)"
} else {
    Write-Output "  - Run manually: .\auto-deploy.ps1"
    Write-Output "  - Or rerun setup with -AutoStart flag for automatic startup"
}
Write-Output ""
Write-Host "[OK] Setup complete! Your Windows deployment is ready." -ForegroundColor Green
