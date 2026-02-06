# Windows Deployment Setup Guide

This guide explains how to set up automated deployment on your Windows machine using a simple, secure polling approach.

## 🚀 One-Command Setup

**Run this command in PowerShell as Administrator:**

```powershell
# Download and run the setup script
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/jimtin/zetherion-ai/main/setup-windows-deployment.ps1" -OutFile "$env:TEMP\setup-windows-deployment.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\setup-windows-deployment.ps1"
```

Or if you've already cloned the repo:

```powershell
# Navigate to your repo
cd C:\path\to\zetherion-ai

# Run the setup script
powershell -ExecutionPolicy Bypass -File setup-windows-deployment.ps1
```

## What the Script Does

1. ✅ Verifies prerequisites (Docker, Git, GitHub CLI)
2. ✅ Installs missing tools automatically
3. ✅ Clones the repository to `C:\Zetherion AI`
4. ✅ Creates `.env` file from template
5. ✅ Creates deployment scripts
6. ✅ Sets up auto-deployment polling (every 5 minutes)

## Setup Options

### Custom Deployment Path

```powershell
.\setup-windows-deployment.ps1 -DeploymentPath "D:\MyProjects\Zetherion AI"
```

### Custom Poll Interval

```powershell
# Poll every 10 minutes instead of 5
.\setup-windows-deployment.ps1 -PollIntervalMinutes 10
```

### Enable Auto-Start on Boot

```powershell
.\setup-windows-deployment.ps1 -AutoStart
```

This creates a Windows Scheduled Task that starts the deployment monitor automatically when your computer boots.

### All Options Combined

```powershell
.\setup-windows-deployment.ps1 `
    -DeploymentPath "D:\Zetherion AI" `
    -PollIntervalMinutes 10 `
    -AutoStart
```

## Post-Setup Steps

### 1. Configure Environment Variables

Edit your `.env` file with production credentials:

```powershell
notepad C:\Zetherion AI\.env
```

Required:
- `DISCORD_TOKEN` - Your Discord bot token
- `GEMINI_API_KEY` - Your Gemini API key

Optional:
- `ANTHROPIC_API_KEY` - Claude API key
- `OPENAI_API_KEY` - OpenAI API key

### 2. Test Deployment

```powershell
cd C:\Zetherion AI
.\deploy-windows.ps1
```

### 3. Start Auto-Deployment Monitor

```powershell
.\auto-deploy.ps1
```

This will:
- Poll GitHub every 5 minutes (or your custom interval)
- Check if new commits exist on main branch
- Verify GitHub Actions CI has passed
- Deploy automatically if CI passed
- Skip deployment if CI failed

## Available Commands

After setup, these scripts are available in your deployment directory:

### Deploy Now
```powershell
.\deploy-windows.ps1
```
Pulls latest code and hot-swaps containers.

### Quick Deploy (No Build)
```powershell
.\deploy-windows.ps1 -NoBuild
```
Restarts containers without rebuilding (faster).

### Start Bot
```powershell
.\start.ps1
```
Starts the bot containers.

### Stop Bot
```powershell
.\stop.ps1
```
Stops the bot containers.

### View Logs
```powershell
.\logs.ps1
```
Shows live logs from the bot (Ctrl+C to exit).

### Start Auto-Deploy Monitor
```powershell
.\auto-deploy.ps1
```
Starts the deployment monitor (polls every 5 minutes by default).

Custom interval:
```powershell
.\auto-deploy.ps1 -IntervalMinutes 10
```

## How Automatic Deployment Works

### Polling-Based Approach

1. **Monitor runs** on your Windows machine (in PowerShell window or as scheduled task)
2. **Every 5 minutes** (configurable):
   - Checks GitHub for new commits on main branch
   - If new commits found, checks GitHub Actions CI status
   - If CI passed → deploys automatically
   - If CI failed → waits for next poll
   - If CI still running → waits for next poll
3. **Hot-swaps containers** (<1 minute downtime)
4. **Logs all activity** to console

### Why Polling Instead of Self-Hosted Runner?

✅ **More secure** - No inbound connections, no GitHub access to your machine
✅ **Simpler** - Just one PowerShell script, no runner installation
✅ **Easier to control** - Start/stop anytime, runs in visible PowerShell window
✅ **No maintenance** - No runner updates, no token management
✅ **Zero cost** - No GitHub runner overhead

## Monitoring & Management

### Check Monitor Status

If running in a PowerShell window, you'll see output like:

```
[AUTO-DEPLOY] Starting deployment monitor
Poll interval: 5 minutes (300 seconds)
Press Ctrl+C to stop

[INFO] Current commit: a1b2c3d
[INFO] Monitoring for changes...

[12:00:00] No changes. Last deploy: 01:30:00 ago
[12:05:00] No changes. Last deploy: 01:35:00 ago

[DETECTED] New commit: e4f5g6h
[INFO] Checking GitHub Actions CI status...
[INFO] Workflow: CI/CD Pipeline
[INFO] Status: completed
[OK] CI passed! Deploying...
...
[OK] Deployment complete at 12:10:15
```

### Run as Background Task

If you used `-AutoStart`:

```powershell
# Check if scheduled task exists
Get-ScheduledTask -TaskName "Zetherion AI-AutoDeploy"

# Start manually
Start-ScheduledTask -TaskName "Zetherion AI-AutoDeploy"

# Stop manually
Stop-ScheduledTask -TaskName "Zetherion AI-AutoDeploy"

# Remove auto-start
Unregister-ScheduledTask -TaskName "Zetherion AI-AutoDeploy" -Confirm:$false
```

### View Container Status

```powershell
docker ps --filter "name=secureclaw"
```

### View Container Logs

```powershell
docker logs secureclaw-bot --tail 50 --follow
```

### Check Git Status

```powershell
cd C:\Zetherion AI
git status
git log --oneline -10
```

### Verify CI Passed

```powershell
gh run list --limit 5
```

## Troubleshooting

### Monitor Not Detecting Changes

**Cause:** GitHub CLI (`gh`) not authenticated or git fetch failing.

**Solution:**
```powershell
# Authenticate GitHub CLI
gh auth login

# Test git fetch
cd C:\Zetherion AI
git fetch origin main

# Test gh command
gh run list --limit 1
```

### Docker Not Running

```powershell
# Check if Docker is running
docker info

# If not, start Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# Wait 30 seconds
Start-Sleep -Seconds 30

# Verify
docker info
```

### Deployment Failing

```powershell
# Check container logs
docker logs secureclaw-bot --tail 100

# Check Docker Compose logs
docker-compose logs

# Verify .env file exists
Test-Path C:\Zetherion AI\.env

# Check Docker Compose config
docker-compose config
```

### Container Won't Start

```powershell
# Clean restart
cd C:\Zetherion AI
docker-compose down -v
docker system prune -f
docker-compose build --no-cache
docker-compose up -d

# Check logs
docker logs secureclaw-bot --follow
```

### Monitor Keeps Deploying Same Commit

**Cause:** Git state not being updated after deployment.

**Solution:**
```powershell
# Stop monitor (Ctrl+C)
cd C:\Zetherion AI
git fetch origin main
git reset --hard origin/main

# Restart monitor
.\auto-deploy.ps1
```

### CI Status Check Failing

**Cause:** GitHub CLI not properly authenticated or workflow name changed.

**Solution:**
```powershell
# Re-authenticate
gh auth login

# Check workflow runs
gh run list --limit 5

# Check specific commit
gh run list --commit <commit-sha>
```

## Uninstall

### Remove Auto-Deploy Task

```powershell
Unregister-ScheduledTask -TaskName "Zetherion AI-AutoDeploy" -Confirm:$false
```

### Remove Deployment

```powershell
# Stop containers
cd C:\Zetherion AI
docker-compose down -v

# Remove deployment
cd \
Remove-Item -Recurse -Force C:\Zetherion AI
```

## Advanced Configuration

### Change Poll Interval After Setup

Edit the scheduled task:

```powershell
# Get current task
$task = Get-ScheduledTask -TaskName "Zetherion AI-AutoDeploy"

# Update arguments (change -IntervalMinutes value)
$task.Actions[0].Arguments = '-WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Zetherion AI\auto-deploy.ps1" -IntervalMinutes 10'

# Save
Set-ScheduledTask -InputObject $task
```

Or just stop the scheduled task and run manually with a different interval:

```powershell
.\auto-deploy.ps1 -IntervalMinutes 10
```

### Run Monitor in Background (Without Scheduled Task)

```powershell
# Start hidden PowerShell window
Start-Process powershell -ArgumentList "-WindowStyle Hidden -ExecutionPolicy Bypass -File C:\Zetherion AI\auto-deploy.ps1" -WorkingDirectory C:\Zetherion AI
```

### Manual Deployment (Skip CI Check)

```powershell
cd C:\Zetherion AI
git pull origin main
.\deploy-windows.ps1
```

## Security Notes

- ✅ Polling is outbound-only (no open ports, no inbound connections)
- ✅ `.env` file never committed to Git
- ✅ Monitor runs with your user account permissions
- ✅ GitHub Actions secrets not needed (uses local `.env`)
- ✅ No GitHub access to your machine
- ✅ No webhooks or exposed endpoints
- ✅ Simple to audit (single PowerShell script)

## Cost

**$0/month** - Everything runs on your local Windows machine, no cloud costs, no GitHub runner costs.

## Support

For issues:
1. Check [Troubleshooting](#troubleshooting) section above
2. View logs: `.\logs.ps1`
3. Open issue: https://github.com/jimtin/zetherion-ai/issues

---

**Last Updated:** 2026-02-06
**Version:** 2.0.0 (Polling-based deployment)
