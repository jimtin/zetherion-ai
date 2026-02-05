# SecureClaw Windows Deployment Setup Script
# This script automates the entire setup process for Windows deployment
# Run as Administrator: powershell -ExecutionPolicy Bypass -File setup-windows-deployment.ps1

#Requires -RunAsAdministrator

param(
    [string]$DeploymentPath = "C:\SecureClaw",
    [string]$RunnerPath = "C:\actions-runner",
    [switch]$SkipRunnerSetup = $false,
    [switch]$AutoStart = $false
)

$ErrorActionPreference = "Stop"

Write-Host @"
================================================================

     SecureClaw Windows Deployment Setup
     Automated Installation & Configuration

================================================================
"@ -ForegroundColor Cyan

Write-Output ""
Write-Output "This script will:"
Write-Output "  1. Verify prerequisites (Docker, Git, GitHub CLI)"
Write-Output "  2. Clone the SecureClaw repository"
Write-Output "  3. Set up environment configuration"
Write-Output "  4. Install GitHub Actions self-hosted runner"
Write-Output "  5. Create deployment scripts"
Write-Output "  6. Configure auto-deployment workflow"
Write-Output ""
Write-Output "Deployment path: $DeploymentPath"
Write-Output "Runner path: $RunnerPath"
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
    git clone https://github.com/jimtin/sercureclaw.git .
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
# deploy-windows.ps1 - SecureClaw deployment script for Windows
param(
    [switch]$SkipBackup = $false,
    [switch]$NoBuild = $false
)

Write-Host "[DEPLOY] SecureClaw Deployment" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan

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
# start.ps1 - Start SecureClaw containers
docker-compose up -d
Write-Host "[OK] Containers started" -ForegroundColor Green
docker-compose ps
'@

Set-Content -Path "start.ps1" -Value $startScript

# Create stop.ps1
$stopScript = @'
# stop.ps1 - Stop SecureClaw containers
docker-compose down --timeout 30
Write-Host "[OK] Containers stopped" -ForegroundColor Green
'@

Set-Content -Path "stop.ps1" -Value $stopScript

# Create logs.ps1
$logsScript = @'
# logs.ps1 - View SecureClaw logs
docker-compose logs -f secureclaw
'@

Set-Content -Path "logs.ps1" -Value $logsScript

# Create auto-deploy.ps1
$autoDeployScript = @'
# auto-deploy.ps1 - Monitor for changes and auto-deploy
param(
    [int]$IntervalSeconds = 60
)

Write-Host "[AUTO-DEPLOY] Monitor started (checking every $IntervalSeconds seconds)" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow

$lastCommit = git rev-parse HEAD

while ($true) {
    Start-Sleep -Seconds $IntervalSeconds

    git fetch origin main
    $currentCommit = git rev-parse origin/main

    if ($lastCommit -ne $currentCommit) {
        Write-Host "[INFO] New changes detected! Deploying..." -ForegroundColor Green
        .\deploy-windows.ps1
        $lastCommit = $currentCommit
    }
}
'@

Set-Content -Path "auto-deploy.ps1" -Value $autoDeployScript

Write-Host "[OK] Deployment scripts created" -ForegroundColor Green

if (-not $SkipRunnerSetup) {
    Write-Output ""
    Write-Host "[STEP 5] Setting up GitHub Actions runner..." -ForegroundColor Cyan

    Write-Output "Opening GitHub runner registration page..."
    # Use single quotes to prevent ampersand interpretation
    Start-Process 'https://github.com/jimtin/sercureclaw/settings/actions/runners/new?arch=x64&os=win'

    Write-Output ""
    Write-Output "1. Wait for the GitHub page to open in your browser"
    Write-Output "2. On the GitHub page that just opened:"
    Write-Output "   - Select: Windows (x64)"
    Write-Output "   - Copy the TOKEN from the Configure command (it starts with 'A')"
    Write-Output ""

    $token = Read-Host "Paste the registration token here"

    if ([string]::IsNullOrWhiteSpace($token)) {
        Write-Host "[WARNING] No token provided. Skipping runner setup." -ForegroundColor Yellow
        Write-Output "You can set up the runner manually later by running:"
        Write-Output "  powershell -File setup-runner.ps1"
    } else {
        # Create runner directory
        if (Test-Path $RunnerPath) {
            Write-Host "[WARNING] Runner directory already exists: $RunnerPath" -ForegroundColor Yellow
            $removeRunner = Read-Host "Remove and reinstall? (yes/no)"
            if ($removeRunner -eq "yes") {
                Remove-Item -Recurse -Force $RunnerPath
            } else {
                Write-Output "Using existing runner directory"
            }
        }

        if (-not (Test-Path $RunnerPath)) {
            New-Item -ItemType Directory -Path $RunnerPath | Out-Null
        }

        Set-Location $RunnerPath

        # Download runner
        Write-Output "Downloading GitHub Actions runner..."
        $runnerZip = "actions-runner-win-x64-2.311.0.zip"
        Invoke-WebRequest -Uri "https://github.com/actions/runner/releases/download/v2.311.0/$runnerZip" -OutFile $runnerZip

        # Extract runner
        Write-Output "Extracting runner..."
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory("$RunnerPath\$runnerZip", $RunnerPath)

        # Configure runner
        Write-Output "Configuring runner..."
        $configCmd = ".\config.cmd --url https://github.com/jimtin/sercureclaw --token $token --name windows-production --work _work --runasservice"

        $process = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $configCmd" -Wait -PassThru -NoNewWindow

        if ($process.ExitCode -eq 0) {
            Write-Output "Installing runner as Windows service..."
            .\svc.sh install

            Write-Output "Starting runner service..."
            .\svc.sh start

            Write-Host "[OK] Runner service started" -ForegroundColor Green

            # Return to deployment directory
            Set-Location $DeploymentPath
        } else {
            Write-Host "[ERROR] Failed to configure runner. Please set up manually." -ForegroundColor Red
        }
    }
} else {
    Write-Host "[WARNING] Skipped runner setup (SkipRunnerSetup flag specified)" -ForegroundColor Yellow
}

Write-Output ""
Write-Host "[STEP 6] Creating deployment workflow..." -ForegroundColor Cyan

# Create .github/workflows directory if it doesn't exist
$workflowDir = ".github\workflows"
if (-not (Test-Path $workflowDir)) {
    New-Item -ItemType Directory -Path $workflowDir -Force | Out-Null
}

# Create deploy-windows.yml workflow
$deployWorkflow = @'
name: Deploy to Windows

on:
  workflow_run:
    workflows: ["CI/CD Pipeline"]
    types:
      - completed
    branches: [main]

jobs:
  deploy:
    name: Deploy to Windows Production
    runs-on: self-hosted
    if: ${{ github.event.workflow_run.conclusion == 'success' }}

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Deploy
        run: .\deploy-windows.ps1
        shell: powershell

      - name: Health check
        run: |
          Start-Sleep -Seconds 15
          docker-compose ps
        shell: powershell
'@

Set-Content -Path "$workflowDir\deploy-windows.yml" -Value $deployWorkflow

Write-Host "[OK] Deployment workflow created" -ForegroundColor Green

# Create auto-start task if requested
if ($AutoStart) {
    Write-Output ""
    Write-Host "[INFO] Creating auto-start scheduled task..." -ForegroundColor Cyan

    $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File `"$DeploymentPath\start.ps1`""
    $taskTrigger = New-ScheduledTaskTrigger -AtStartup
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    try {
        Register-ScheduledTask -TaskName "SecureClaw-AutoStart" -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
        Write-Host "[OK] Created auto-start scheduled task" -ForegroundColor Green
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
if (-not $SkipRunnerSetup) {
    Write-Output "Runner directory: $RunnerPath"
}
Write-Output ""
Write-Output "Quick Commands:"
Write-Output "  Deploy now:           .\deploy-windows.ps1"
Write-Output "  Start bot:            .\start.ps1"
Write-Output "  Stop bot:             .\stop.ps1"
Write-Output "  View logs:            .\logs.ps1"
Write-Output "  Auto-deploy monitor:  .\auto-deploy.ps1"
Write-Output ""
Write-Output "Next Steps:"
Write-Output "  1. Test deployment:   .\deploy-windows.ps1"
Write-Output "  2. Verify bot works:  .\logs.ps1"
Write-Output "  3. Commit workflow:   git add .github/workflows/deploy-windows.yml"
Write-Output "                        git commit -m 'feat: add Windows deployment workflow'"
Write-Output "                        git push origin main"
Write-Output ""
Write-Output "Automatic Deployment:"
Write-Output "  - Triggers when CI passes on main branch"
Write-Output "  - Runner service runs in background"
Write-Output "  - Check status: Set-Location $RunnerPath; .\svc.sh status"
Write-Output ""
Write-Host "[OK] Setup complete! Your Windows deployment is ready." -ForegroundColor Green
