# Zetherion AI Troubleshooting Guide

Common issues and their solutions for running Zetherion AI.

## Table of Contents
- [Discord Errors](#discord-errors)
- [Configuration Issues](#configuration-issues)
- [Docker Issues](#docker-issues)
- [Ollama Issues](#ollama-issues)
- [Qdrant Connection Issues](#qdrant-connection-issues)
- [Python Version Issues](#python-version-issues)
- [API Key Issues](#api-key-issues)
- [Performance Issues](#performance-issues)

---

## Discord Errors

### Error: `PrivilegedIntentsRequired`

**Full Error:**
```
discord.errors.PrivilegedIntentsRequired: Shard ID None is requesting privileged intents
that have not been explicitly enabled in the developer portal.
```

**Cause:** The bot needs the Message Content Intent to read messages, but it's not enabled in Discord.

**Solution:**
1. Go to https://discord.com/developers/applications
2. Select your bot application
3. Go to **Bot** tab
4. Scroll to **Privileged Gateway Intents**
5. **Enable "MESSAGE CONTENT INTENT"** (toggle it ON)
6. Click **Save Changes**
7. Restart the bot: `./stop.sh && ./start.sh`

**Why Required:** Discord requires explicit permission to read message content for privacy/security.

---

### Bot Not Responding in Server

**Symptoms:**
- Bot is online but doesn't respond to mentions
- Slash commands work but messages don't

**Solutions:**

1. **Check Bot Permissions:**
   - Bot needs `Send Messages`, `Read Messages`, `Embed Links` permissions
   - Right-click the bot in server → Manage → Check role permissions

2. **Verify Message Content Intent:**
   - See [PrivilegedIntentsRequired](#error-privilegedintentsrequired) above

3. **Check if Bot is Being Mentioned:**
   - Bot only responds to DMs or when mentioned: `@BotName your message`

4. **Check Allowlist:**
   - If `ALLOWED_USER_IDS` is set in `.env`, your user ID must be included
   - To allow all users: `ALLOWED_USER_IDS=` (leave empty)

---

### Slash Commands Not Appearing

**Symptoms:**
- Can't see `/ask`, `/remember`, `/search` commands

**Solutions:**

1. **Wait for Sync:**
   - Commands can take up to 1 hour to appear globally
   - Restart Discord app to force refresh

2. **Check Bot Scope:**
   - When you invited the bot, did you select `applications.commands` scope?
   - If not, generate new invite URL with both `bot` and `applications.commands`

3. **Reinvite Bot:**
   - Go to OAuth2 → URL Generator in Discord Developer Portal
   - Select: `bot` + `applications.commands`
   - Use new URL to invite bot

4. **Check Logs:**
   ```bash
   # Look for "commands_synced" message
   ./status.sh
   ```

---

## Configuration Issues

### Error: `error parsing value for field "allowed_user_ids"`

**Full Error:**
```
pydantic_settings.sources.SettingsError: error parsing value for field "allowed_user_ids"
```

**Cause:** Invalid format for `ALLOWED_USER_IDS` in `.env` file.

**Solution:**
```bash
# Correct formats:
ALLOWED_USER_IDS=                        # Allow all users (empty)
ALLOWED_USER_IDS=123456789               # Single user
ALLOWED_USER_IDS=123456789,987654321     # Multiple users (comma-separated, no spaces)

# Wrong formats:
ALLOWED_USER_IDS=123456789, 987654321    # Extra spaces after comma
ALLOWED_USER_IDS=[123456789]             # JSON format (not supported)
```

**Get Your Discord User ID:**
1. Enable Developer Mode in Discord: Settings → Advanced → Developer Mode
2. Right-click your username anywhere
3. Select "Copy User ID"
4. Paste into `.env`: `ALLOWED_USER_IDS=your_id_here`

---

### Missing Environment Variables

**Error:**
```
Field required [type=missing]
```

**Solution:**

1. **Check Required Variables:**
   - `DISCORD_TOKEN` (required)
   - `GEMINI_API_KEY` (required)

2. **Optional Variables:**
   - `ANTHROPIC_API_KEY` (for Claude)
   - `OPENAI_API_KEY` (for GPT-4)
   - `ALLOWED_USER_IDS` (defaults to allow all)
   - `QDRANT_HOST` (defaults to "qdrant")
   - `QDRANT_PORT` (defaults to 6333)

3. **Verify `.env` File Exists:**
   ```bash
   ls -la .env
   # If missing:
   cp .env.example .env
   ```

4. **Check for Trailing Spaces:**
   ```bash
   # Bad:
   DISCORD_TOKEN=abc123

   # Good:
   DISCORD_TOKEN=abc123
   ```

---

## Qdrant Connection Issues

### Error: Connection Refused to Qdrant

**Full Error:**
```
ConnectionRefusedError: [Errno 61] Connection refused
httpcore._exceptions.ConnectError: [Errno 61] Connection refused
```

**Cause:** Bot can't connect to Qdrant vector database.

**Solutions:**

1. **Check Qdrant is Running:**
   ```bash
   docker ps | grep qdrant
   # Should show zetherion_ai-qdrant running
   ```

2. **Start Qdrant if Stopped:**
   ```bash
   docker start zetherion_ai-qdrant
   # Or use the start script:
   ./start.sh
   ```

3. **Check `QDRANT_HOST` Setting:**

   **For Local Development (./start.sh):**
   ```bash
   # In .env:
   QDRANT_HOST=localhost
   QDRANT_PORT=6333
   ```

   **For Docker Compose:**
   ```bash
   # In .env:
   QDRANT_HOST=qdrant
   QDRANT_PORT=6333
   ```

4. **Verify Qdrant Health:**
   ```bash
   curl http://localhost:6333/healthz
   # Should return: healthy
   ```

5. **Check Port Availability:**
   ```bash
   lsof -i :6333
   # Should show Docker using port 6333
   ```

6. **Restart Qdrant:**
   ```bash
   docker restart zetherion_ai-qdrant
   # Wait 10 seconds
   curl http://localhost:6333/healthz
   ```

---

### Qdrant Data Persistence

**Symptoms:**
- Bot loses all memories after restart
- Collections disappear

**Cause:** Qdrant data not being persisted to disk.

**Solution:**

1. **Check Volume Mount:**
   ```bash
   docker inspect zetherion_ai-qdrant | grep -A 5 Mounts
   # Should show volume mounted to /qdrant/storage
   ```

2. **Verify Data Directory:**
   ```bash
   ls -la qdrant_storage/
   # Should contain Qdrant database files
   ```

3. **Recreate with Proper Volume:**
   ```bash
   docker stop zetherion_ai-qdrant
   docker rm zetherion_ai-qdrant
   ./start.sh  # Will recreate with volume
   ```

---

## Python Version Issues

### Error: `Package requires a different Python`

**Full Error:**
```
ERROR: Package 'zetherion_ai' requires a different Python: 3.11.6 not in '>=3.12'
```

**Cause:** Zetherion AI requires Python 3.12+, but you have an older version.

**Solutions:**

1. **Install Python 3.12+ (macOS with Homebrew):**
   ```bash
   brew install python@3.12
   ```

2. **Verify Installation:**
   ```bash
   python3.12 --version
   # Should show: Python 3.12.x
   ```

3. **Recreate Virtual Environment:**
   ```bash
   rm -rf .venv
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install -e .
   ```

4. **Use start.sh (Automatic):**
   ```bash
   ./start.sh
   # Automatically finds Python 3.12+
   ```

---

### Multiple Python Versions

**Symptoms:**
- `python3 --version` shows old version
- But `python3.12` exists

**Solution:**
The start script handles this automatically. If you need to manually use Python 3.12:

```bash
# Always use explicit version:
python3.12 -m venv .venv
source .venv/bin/activate

# Or use the start script:
./start.sh
```

---

## API Key Issues

### Invalid Discord Token

**Error:**
```
discord.errors.LoginFailure: Improper token has been passed
```

**Solutions:**

1. **Regenerate Token:**
   - Go to Discord Developer Portal → Bot tab
   - Click "Reset Token"
   - Copy new token immediately
   - Update `.env`: `DISCORD_TOKEN=new_token_here`

2. **Check for Extra Spaces:**
   ```bash
   # Bad:
   DISCORD_TOKEN= abc123
   DISCORD_TOKEN=abc123

   # Good:
   DISCORD_TOKEN=abc123
   ```

3. **Verify Token Format:**
   - Three parts separated by dots (Base64-encoded)
   - Format: `<FIRST_PART>.<SECOND_PART>.<THIRD_PART>`
   - Each part contains alphanumeric characters, hyphens, and underscores
   - Real tokens are much longer (70+ characters total)
   - No quotes needed in `.env`
   - **Never share your actual token!**

---

### Invalid Gemini API Key

**Error:**
```
google.api_core.exceptions.PermissionDenied: 403 API key not valid
```

**Solutions:**

1. **Verify API Key:**
   - Go to https://aistudio.google.com/app/apikey
   - Check if key exists and is enabled
   - Generate new key if needed

2. **Check Gemini API Quotas:**
   - Free tier has limits
   - Check quota at: https://aistudio.google.com/app/apikey

3. **Enable Gemini API:**
   - Some Google accounts need to enable the API first
   - Visit: https://makersuite.google.com/

---

### Rate Limiting

**Error:**
```
429 Too Many Requests
anthropic.RateLimitError: rate_limit_error
```

**Cause:** Too many API requests in short time.

**Solutions:**

1. **Wait Before Retrying:**
   - The bot has automatic retry with backoff
   - Wait 1-2 minutes before trying again

2. **Reduce Usage:**
   - Gemini Flash: 15 requests/minute (free tier)
   - Claude: Depends on your tier
   - OpenAI: Depends on your tier

3. **Check API Dashboard:**
   - Anthropic: https://console.anthropic.com/
   - OpenAI: https://platform.openai.com/usage

4. **Upgrade API Tier:**
   - Most rate limits are per-tier
   - Free → Paid tier often increases limits significantly

---

## Performance Issues

### Slow Response Times

**Symptoms:**
- Bot takes 10+ seconds to respond
- Timeouts in Discord

**Solutions:**

1. **Check Which Model is Being Used:**
   - Simple queries → Gemini Flash (fast)
   - Complex tasks → Claude/GPT-4 (slower)

2. **Adjust Router Threshold:**
   - Edit `src/zetherion_ai/agent/router.py`
   - Line 136: Change `confidence > 0.7` to `0.8` to use Flash more often

3. **Check Qdrant Performance:**
   ```bash
   curl http://localhost:6333/metrics
   # Check for slow queries
   ```

4. **Reduce Memory Context:**
   - Edit `src/zetherion_ai/agent/core.py`
   - Line ~145: Reduce `memory_limit` and `history_limit`

---

### High Memory Usage

**Symptoms:**
- Bot using multiple GB of RAM
- System slowing down

**Solutions:**

1. **Check Qdrant Memory:**
   ```bash
   docker stats zetherion_ai-qdrant
   ```

2. **Limit Qdrant Memory:**
   ```bash
   # Stop and recreate with memory limit:
   docker stop zetherion_ai-qdrant
   docker rm zetherion_ai-qdrant
   docker run -d \
     --name zetherion_ai-qdrant \
     -p 6333:6333 \
     --memory="2g" \
     -v $(pwd)/qdrant_storage:/qdrant/storage \
     qdrant/qdrant:latest
   ```

3. **Clear Old Memories:**
   ```bash
   # Delete qdrant_storage and restart:
   rm -rf qdrant_storage
   ./start.sh
   ```

---

## Docker Issues

### Docker Not Running

**Error:**
```
Cannot connect to the Docker daemon
```

**Solution:**

**Automatic (Recommended):**
```bash
./start.sh
# The script will detect Docker is not running and:
# 1. Launch Docker Desktop automatically
# 2. Wait for daemon to be ready (up to 90 seconds)
# 3. Continue with setup
```

**Manual:**
1. Open Docker Desktop application
2. Wait for it to fully start (green icon in menu bar)
3. Verify: `docker ps`
4. Retry: `./start.sh`

**If Docker Desktop won't start:**
- Check Activity Monitor for stuck Docker processes
- Try: `killall Docker` then relaunch
- Check Console.app for Docker error logs
- Reinstall Docker Desktop if necessary

---

### Docker Daemon Not Ready After Launch

**Symptoms:**
- Docker Desktop GUI is open
- `docker info` returns connection error
- startup script times out waiting for daemon

**Causes:**
- Docker still initializing (can take 30-60 seconds on cold start)
- Docker settings corrupted
- Insufficient system resources

**Solutions:**

1. **Wait Longer:**
   ```bash
   # The start script waits up to 90 seconds
   # If you manually started Docker, wait before running commands:
   for i in {1..60}; do
     docker info >/dev/null 2>&1 && echo "Ready!" && break
     echo "Waiting... ($i/60)"
     sleep 1
   done
   ```

2. **Check Docker Desktop Status:**
   - Look at menu bar icon
   - Should show green icon with no error messages
   - If yellow or red, click for details

3. **Restart Docker Desktop:**
   ```bash
   osascript -e 'quit app "Docker"'
   sleep 5
   open -a Docker
   ```

4. **Check Available Resources:**
   ```bash
   # macOS:
   vm_stat | head -3
   df -h /  # Disk space

   # Ensure you have:
   # - At least 2GB free RAM
   # - At least 10GB free disk space
   ```

5. **Reset Docker (if stuck):**
   - Docker Desktop → Troubleshoot → Reset to factory defaults
   - **WARNING:** Deletes all containers/images
   - Run `./start.sh` to rebuild

---

### Docker Desktop Memory Allocation

**How much memory should Docker have?**

**Depends on router backend:**

**Gemini (Cloud):**
- Minimum: 2GB (for Qdrant only)
- Recommended: 4GB (safe buffer)

**Ollama (Local):**
- Depends on model selected
- See recommendations:
  - `phi3:mini`: 5GB
  - `llama3.1:8b`: 8GB
  - `qwen2.5:7b`: 10GB
  - `mistral:7b`: 7GB

**Automated Management:**
```bash
# The startup script handles this automatically:
./start.sh

# It will:
# 1. Detect your selected model
# 2. Check Docker's current allocation
# 3. Prompt to increase if needed
# 4. Automatically update Docker settings
# 5. Restart Docker if required
```

**Manual Check:**
```bash
# Check current allocation:
docker info | grep "Total Memory"

# Check what's required:
grep OLLAMA_DOCKER_MEMORY .env
```

**See also:** [Docker Architecture](DOCKER_ARCHITECTURE.md#memory-hierarchy) for detailed explanation.

---

### Port Already in Use

**Error:**
```
Error starting userland proxy: listen tcp 0.0.0.0:6333: bind: address already in use
```

**Solutions:**

1. **Find What's Using Port 6333:**
   ```bash
   lsof -i :6333
   ```

2. **Kill the Process:**
   ```bash
   # Get PID from lsof, then:
   kill <PID>
   ```

3. **Use Different Port:**
   ```bash
   # In .env:
   QDRANT_PORT=6334

   # Recreate Qdrant:
   docker rm -f zetherion_ai-qdrant
   docker run -d \
     --name zetherion_ai-qdrant \
     -p 6334:6333 \
     -v $(pwd)/qdrant_storage:/qdrant/storage \
     qdrant/qdrant:latest
   ```

---

## Ollama Issues

### Ollama Container Fails with "Out of Memory"

**Error:**
```
Error: llama runner process has terminated: signal: killed
Server error: '500 Internal Server Error'
```

**Cause:** Docker Desktop doesn't have enough RAM allocated for the Ollama model.

**Solution:**

The startup script should handle this automatically, but if you encounter it manually:

1. **Check Docker Desktop Memory:**
   ```bash
   docker info | grep "Total Memory"
   # Example output: Total Memory: 4 GiB
   ```

2. **Increase Docker Desktop Memory:**
   ```bash
   # Automated approach (recommended):
   cd scripts
   ./increase-docker-memory.sh

   # This will:
   # - Detect required memory from .env
   # - Update Docker Desktop settings
   # - Restart Docker automatically
   ```

3. **Manual Approach (if script fails):**
   - Open Docker Desktop
   - Go to **Settings** → **Resources** → **Advanced**
   - Increase **Memory** slider to match your model's requirement:
     - `phi3:mini` → 5GB minimum
     - `llama3.1:8b` → 8GB minimum
     - `qwen2.5:7b` → 10GB minimum
     - `mistral:7b` → 7GB minimum
   - Click **Apply & Restart**
   - Wait for Docker to restart (30-60 seconds)

4. **Verify the Change:**
   ```bash
   docker info | grep "Total Memory"
   # Should show your new allocation
   ```

**See also:** [Docker Architecture](DOCKER_ARCHITECTURE.md) for understanding Docker Desktop vs container memory.

---

### Ollama Model Download Fails

**Error:**
```
Failed to download model 'qwen2.5:7b'
pulling manifest: Get "https://registry.ollama.ai/v2/library/qwen2.5/manifests/7b": EOF
```

**Causes:**
- Network connectivity issues
- Registry temporarily unavailable
- Insufficient disk space

**Solutions:**

1. **Check Internet Connection:**
   ```bash
   curl -I https://ollama.ai
   # Should return: HTTP/2 200
   ```

2. **Check Disk Space:**
   ```bash
   df -h .
   # Ensure at least 10GB free for large models
   ```

3. **Manually Pull Model:**
   ```bash
   # If automatic pull failed, try manually:
   docker exec zetherion_ai-ollama ollama pull qwen2.5:7b

   # For smaller model:
   docker exec zetherion_ai-ollama ollama pull phi3:mini
   ```

4. **Use Different Model:**
   ```bash
   # Switch to smaller model:
   rm .ollama_assessed
   # Edit .env to prefer smaller model
   echo "OLLAMA_ROUTER_MODEL=phi3:mini" >> .env
   ./start.sh
   ```

5. **Fallback to Gemini:**
   - The bot automatically falls back to Gemini if Ollama model unavailable
   - Change router backend in `.env`:
     ```bash
     ROUTER_BACKEND=gemini
     ```

---

### Ollama Connection Error

**Error:**
```
[Errno 8] nodename nor servname provided, or not known
httpx.ConnectError: [Errno 61] Connection refused
```

**Cause:** Bot can't connect to Ollama container.

**Solutions:**

1. **Check Ollama Container is Running:**
   ```bash
   docker ps | grep ollama
   # Should show: zetherion_ai-ollama
   ```

2. **Start Ollama Container:**
   ```bash
   docker start zetherion_ai-ollama
   # Or use the start script:
   ./start.sh
   ```

3. **Check `OLLAMA_HOST` Setting:**

   **For Local Development (./start.sh):**
   ```bash
   # In .env:
   OLLAMA_HOST=localhost
   OLLAMA_PORT=11434
   ```

   **For Docker Compose:**
   ```bash
   # In .env:
   OLLAMA_HOST=ollama
   OLLAMA_PORT=11434
   ```

4. **Verify Ollama API:**
   ```bash
   curl http://localhost:11434/api/tags
   # Should return JSON with list of models
   ```

5. **Check Container Logs:**
   ```bash
   docker logs zetherion_ai-ollama
   # Look for errors or OOM messages
   ```

---

### Ollama Slow Response Times

**Symptoms:**
- Ollama takes 30+ seconds to respond
- Slower than expected inference

**Solutions:**

1. **Check CPU Usage:**
   ```bash
   docker stats zetherion_ai-ollama
   # Look at CPU% - should be 100-400% during inference
   ```

2. **Verify Model Size:**
   ```bash
   docker exec zetherion_ai-ollama ollama list
   # Smaller models (phi3:mini) are faster than large ones
   ```

3. **Switch to Smaller Model:**
   ```bash
   # Remove assessment marker to choose again:
   rm .ollama_assessed
   ./start.sh
   # Select phi3:mini or mistral:7b when prompted
   ```

4. **Check Docker Memory:**
   ```bash
   docker stats zetherion_ai-ollama
   # MEM USAGE should be well below LIMIT
   # If at limit, model is swapping (very slow)
   ```

5. **Add More Docker Memory:**
   ```bash
   # See "Ollama Container Fails with Out of Memory" above
   ./scripts/increase-docker-memory.sh
   ```

6. **Use GPU Acceleration (if available):**
   - Requires NVIDIA GPU with Docker GPU support
   - Or Apple Silicon Mac (uses Metal automatically)
   - Check logs for "GPU detected" message

---

### Docker Desktop Won't Start After Memory Increase

**Symptoms:**
- Ran `increase-docker-memory.sh`
- Docker Desktop GUI opens but daemon never becomes ready
- Stuck at "Docker Desktop is starting..."

**Possible Causes:**
- Requested more RAM than system has available
- Other applications using too much memory
- Docker settings corrupted

**Solutions:**

1. **Check System RAM Availability:**
   ```bash
   # macOS:
   vm_stat | head -2
   # Look at "Pages free"
   ```

2. **Restore Backup Settings:**
   ```bash
   # increase-docker-memory.sh creates backups
   cd ~/Library/Group\ Containers/group.com.docker/
   ls -lt settings.json.backup.*

   # Restore most recent backup:
   cp settings.json.backup.20260205_143022 settings.json
   ```

3. **Manually Reduce Memory:**
   ```bash
   # Edit settings file:
   vim ~/Library/Group\ Containers/group.com.docker/settings.json

   # Find "memoryMiB" and set to safe value:
   {
     "memoryMiB": 6144,  // 6GB - usually safe
     ...
   }
   ```

4. **Restart Docker:**
   ```bash
   # Quit Docker Desktop
   osascript -e 'quit app "Docker"'
   sleep 5

   # Relaunch
   open -a Docker

   # Wait for daemon
   for i in {1..60}; do
     docker info >/dev/null 2>&1 && echo "Ready!" && break
     sleep 1
   done
   ```

5. **Reset Docker Desktop (Last Resort):**
   - Open Docker Desktop
   - Go to **Troubleshoot** → **Reset to factory defaults**
   - **WARNING:** This deletes all containers and images
   - After reset, run `./start.sh` to recreate everything

6. **Close Other Memory-Intensive Apps:**
   - Quit Chrome, Slack, IDEs, etc.
   - Check Activity Monitor for memory hogs
   - Try increasing Docker memory again with more free RAM

---

### Switch Between Gemini and Ollama

**How to change router backend after initial setup:**

**Switch to Ollama:**
```bash
# 1. Edit .env
sed -i '' 's/ROUTER_BACKEND=gemini/ROUTER_BACKEND=ollama/' .env

# 2. Run assessment (if not done before)
rm .ollama_assessed  # Force re-assessment
./start.sh
```

**Switch to Gemini:**
```bash
# 1. Edit .env
sed -i '' 's/ROUTER_BACKEND=ollama/ROUTER_BACKEND=gemini/' .env

# 2. Restart bot
./stop.sh && ./start.sh
```

**Or manually edit `.env`:**
```bash
# Change this line:
ROUTER_BACKEND=gemini  # or ollama
```

---

## Getting Help

### Collecting Debug Information

Before asking for help, gather this information:

```bash
# 1. Check status
./status.sh

# 2. Get bot logs (last 50 lines)
docker logs zetherion_ai-qdrant --tail 50

# 3. Check Python version
python3 --version

# 4. Check Docker version
docker --version

# 5. Test Qdrant
curl http://localhost:6333/healthz

# 6. Check disk space
df -h .
```

### Enable Debug Logging

```bash
# In .env:
LOG_LEVEL=DEBUG

# Restart:
./stop.sh && ./start.sh
```

### Report an Issue

Include:
- OS version (macOS/Linux)
- Python version
- Full error message (last 20 lines)
- Steps to reproduce
- What you've already tried

---

## Quick Reference

### Common Commands

```bash
# Start bot
./start.sh

# Check status
./status.sh

# Stop bot
./stop.sh

# View logs
tail -f logs/zetherion_ai.log  # if logging to file

# Restart bot
./stop.sh && ./start.sh

# Check Qdrant health
curl http://localhost:6333/healthz

# List Docker containers
docker ps -a

# View bot process
ps aux | grep zetherion_ai
```

### Configuration Checklist

- [ ] Discord Token in `.env`
- [ ] Gemini API Key in `.env`
- [ ] Message Content Intent enabled in Discord
- [ ] Bot invited with `applications.commands` scope
- [ ] Router backend selected (`ROUTER_BACKEND` in `.env`)
- [ ] Qdrant running on correct host/port
- [ ] Ollama container running (if using Ollama backend)
- [ ] Docker Desktop has sufficient memory (check `docker info`)
- [ ] Python 3.12+ installed
- [ ] Docker Desktop running
- [ ] Correct `QDRANT_HOST` for your setup
- [ ] Correct `OLLAMA_HOST` for your setup (if using Ollama)

---

## Still Having Issues?

1. Check the [README.md](../README.md) for setup instructions
2. Enable debug logging: `LOG_LEVEL=DEBUG` in `.env`
3. Search existing GitHub issues
4. Create new issue with debug information
