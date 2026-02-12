# Windows Remote Installation Log

**Date**: 2026-02-10
**Source Machine**: macOS (<WINDOWS_HOST_IP_OLD>)
**Target Machine**: Windows 11 - "Computer-of-awesome" (<WINDOWS_HOST_IP>)
**Windows User**: james
**Method**: SSH remoting from macOS to Windows PowerShell
**Result**: SUCCESS - All 6 services healthy, bot connected to Discord

---

## Prerequisites

### On Windows (one-time setup, run as Administrator)

1. **Enable OpenSSH Server**:
   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Start-Service sshd
   Set-Service -Name sshd -StartupType Automatic
   ```

2. **Set PowerShell as default SSH shell**:
   ```powershell
   New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
   ```

3. **Add SSH public key** (for admin user):
   ```powershell
   $authorizedKeysFile = "$env:ProgramData\ssh\administrators_authorized_keys"
   Add-Content -Path $authorizedKeysFile -Value "<your-public-key>"
   icacls $authorizedKeysFile /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
   ```

4. **Restart SSH service**:
   ```powershell
   Restart-Service sshd
   ```

5. **Network profile**: Ensure Windows network is set to **Private** (not Public), otherwise firewall blocks inbound SSH.

6. **Firewall rule** (if SSH still blocked after setting Private):
   ```powershell
   New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
   ```

### On macOS (one-time setup)

1. **Generate SSH key**:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/zetherion_windows -N "" -C "zetherion-remote-install"
   ```

2. **Copy public key** to the Windows machine (paste into step 3 above):
   ```bash
   cat ~/.ssh/zetherion_windows.pub
   ```

3. **Test connection**:
   ```bash
   ssh -i ~/.ssh/zetherion_windows -o StrictHostKeyChecking=accept-new james@<WINDOWS_IP> "whoami; hostname"
   ```

### Verified Prerequisites on Windows
- Docker Desktop 29.2.0
- Git 2.33.0
- GitHub CLI 2.86.0
- 63GB RAM

---

## Installation Steps

All commands below use this SSH prefix (abbreviated as `SSH` in examples):
```bash
SSH="ssh -i ~/.ssh/zetherion_windows -o ConnectTimeout=10 james@<WINDOWS_HOST_IP>"
```

### Step 1: Start Docker Desktop (if not running)
```bash
$SSH "Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'"
# Wait ~60 seconds for Docker to initialize
$SSH "docker info | Select-String 'Server Version'"
```

### Step 2: Disable Docker Credential Store (required for SSH sessions)
Docker Desktop's credential helper uses Windows Credential Manager, which is not accessible over SSH. Temporarily disable it:
```bash
$SSH "Copy-Item ~\.docker\config.json ~\.docker\config.json.bak"
$SSH "(Get-Content ~\.docker\config.json) -replace '\"credsStore\": \"desktop\"', '\"credsStore\": \"\"' | Set-Content ~\.docker\config.json"
```

### Step 3: Clone Repository
```bash
$SSH "git clone https://github.com/jimtin/zetherion-ai.git C:\ZetherionAI"
```

### Step 4: Configure Environment
Option A - Copy .env from existing installation:
```bash
scp -i ~/.ssh/zetherion_windows /path/to/local/.env james@<WINDOWS_HOST_IP>:C:/ZetherionAI/.env
```

Option B - Create from template and edit:
```bash
$SSH "Copy-Item C:\ZetherionAI\.env.example C:\ZetherionAI\.env"
# Then edit via RDP or use PowerShell to set values
```

Minimum required keys: `DISCORD_TOKEN`, `GEMINI_API_KEY`, `ENCRYPTION_PASSPHRASE`

### Step 5: Build and Start Services
```bash
$SSH "cd C:\ZetherionAI; docker compose up -d --build"
```
This pulls base images (~3.2GB for Ollama x2, ~200MB for Postgres, ~150MB for Qdrant), builds the bot and skills images, and starts all 6 containers.

### Step 6: Verify All Services Healthy
```bash
$SSH "cd C:\ZetherionAI; docker compose ps --format 'table {{.Name}}\t{{.Status}}'"
```
Expected: All 6 containers show `(healthy)`.

### Step 7: Pull Ollama Models
These can be pulled in parallel:
```bash
# Router model (2.0GB)
$SSH "docker exec zetherion-ai-ollama-router ollama pull llama3.2:3b"

# Generation model (4.9GB)
$SSH "docker exec zetherion-ai-ollama ollama pull llama3.1:8b"

# Embedding model (274MB)
$SSH "docker exec zetherion-ai-ollama ollama pull nomic-embed-text"
```

### Step 7b: Warm Up Ollama Models (Critical)
After pulling, models must be loaded into memory before the bot can use them.
The first load from disk can take 30-60+ seconds per model, which exceeds the
bot's default 30-second timeout. Warm them up explicitly:
```bash
# Warm up router model (loads ~2.0GB into RAM)
$SSH "docker exec zetherion-ai-ollama-router ollama run llama3.2:3b 'Say hi'"

# Warm up generation model (loads ~4.9GB into RAM)
$SSH "docker exec zetherion-ai-ollama ollama run llama3.1:8b 'Say hi'"

# Warm up embedding model (loads ~274MB into RAM)
$SSH "docker exec zetherion-ai-ollama ollama run nomic-embed-text 'test'"
```
**Why this matters**: Without warmup, the first Discord message will trigger a
cold model load. With a 30-second Ollama timeout, the router classification
times out, then the generation request times out — resulting in 60 seconds of
waiting followed by an error message. The bot's built-in keep-warm task runs
every 5 minutes, but only works once models are already loaded.

### Step 8: Verify Bot Connected to Discord
```bash
$SSH "cd C:\ZetherionAI; docker compose logs zetherion-ai-bot --tail 5"
```
Look for: `"event": "bot_ready"` with your bot's username and guild count.

### Step 9: Restore Docker Credential Store

```bash
$SSH "Copy-Item ~\.docker\config.json.bak ~\.docker\config.json -Force"
```

### Step 10: End-to-End Verification

Run this from a container to verify all service-to-service communication works:

```bash
$SSH "docker exec zetherion-ai-skills python -c \"
import urllib.request, json, time

tests = [
    ('Router', 'http://ollama-router:11434/api/generate',
     json.dumps({'model':'llama3.2:3b','prompt':'Say hi','stream':False}).encode()),
    ('Generation', 'http://ollama:11434/api/generate',
     json.dumps({'model':'llama3.1:8b','prompt':'Say hi','stream':False}).encode()),
    ('Embeddings', 'http://ollama:11434/api/embed',
     json.dumps({'model':'nomic-embed-text','input':'test'}).encode()),
    ('Qdrant', 'http://qdrant:6333/collections', None),
    ('Skills', 'http://zetherion-ai-skills:8080/health', None),
]

for name, url, data in tests:
    start = time.time()
    req = urllib.request.Request(url, data=data,
        headers={'Content-Type':'application/json'} if data else {})
    r = urllib.request.urlopen(req, timeout=30)
    print(f'  PASS {name}: {time.time()-start:.1f}s')

print('ALL TESTS PASSED')
\""
```

Expected: All 5 tests pass. Router and generation should respond in <3 seconds each.

---

## Issues Encountered and Fixes

### Issue 1: SSH Connection Timeout
- **Symptom**: `ssh: connect to host <WINDOWS_HOST_IP> port 22: Operation timed out`
- **Cause**: Windows network profile was set to "Public", which blocks inbound connections
- **Fix**: Changed network profile to "Private" in Windows Settings > Network & Internet
- **Note**: Even on the same subnet (both `<WINDOWS_HOST_SUBNET>.x`), Public profile blocks all inbound traffic

### Issue 2: PowerShell `&&` Operator Not Supported
- **Symptom**: `The token '&&' is not a valid statement separator in this version`
- **Cause**: Windows PowerShell (5.x) doesn't support `&&` chaining (PowerShell 7+ does)
- **Fix**: Use `;` (semicolons) to chain commands. Note: `;` runs next command regardless of previous exit code, unlike `&&`

### Issue 3: Docker Credential Store Fails Over SSH
- **Symptom**: `error getting credentials - err: exit status 1, out: 'A specified logon session does not exist'`
- **Cause**: Docker Desktop uses `"credsStore": "desktop"` in `~/.docker/config.json`, which relies on Windows Credential Manager (not available over SSH)
- **Fix**: Temporarily change `credsStore` to `""` in config.json before pulling images, then restore afterward
- **Important**: Restore the original config.json after installation so Docker Desktop works normally via GUI

### Issue 4: docker-compose.yml `version` Warning
- **Symptom**: `the attribute 'version' is obsolete, it will be ignored`
- **Cause**: Docker Compose V2 no longer requires the `version` field
- **Fix**: Removed `version: '3.8'` from docker-compose.yml and docker-compose.dev.yml
- **Note**: This also caused PowerShell to report exit code 1 because it treats stderr output as errors

### Issue 5: "Not Authorized" on Fresh Install (Empty RBAC Table)
- **Symptom**: Bot replies "Sorry, you're not authorized to use this bot." to all messages
- **Cause**: The bot uses PostgreSQL-backed RBAC. On first start, `_bootstrap()` seeds users from `ALLOWED_USER_IDS` and `OWNER_USER_ID` in `.env`. If both are empty, the `users` table is empty and nobody can authenticate.
- **Fix (immediate)**: Insert the user directly into PostgreSQL:
  ```bash
  $SSH "docker exec zetherion-ai-postgres psql -U zetherion -d zetherion -c \"INSERT INTO users (discord_user_id, role, added_by) VALUES (<YOUR_DISCORD_ID>, 'owner', <YOUR_DISCORD_ID>) ON CONFLICT DO NOTHING;\""
  ```
- **Fix (prevent)**: Set `ALLOWED_USER_IDS=<your_discord_id>` in `.env` BEFORE first `docker compose up`. The bootstrap only runs when the `users` table is empty.
- **How to get your Discord User ID**: Enable Developer Mode in Discord Settings > Advanced, then right-click your username > "Copy User ID"

### Issue 6: Ollama Warmup 404 on First Start
- **Symptom**: `ollama_warmup_failed` with 404 error in bot logs
- **Cause**: Ollama containers are running but models haven't been pulled yet
- **Fix**: Pull models (Step 7) - the bot continues to function and will use the models once available
- **Note**: The bot falls back to cloud providers (Gemini/Claude/OpenAI) until local models are ready

### Issue 7: "Error Processing Request" After Model Pull (Cold Load Timeout)
- **Symptom**: Bot replies with an error after ~60 seconds. Logs show `ollama_timeout` followed by `ollama_generation_failed`
- **Cause**: After pulling, the first request triggers a cold load of the model from disk into RAM. For llama3.1:8b (4.9GB) this can take 30-60+ seconds, exceeding the bot's 30-second Ollama timeout. The request chain is: router classification (30s timeout) -> generation (30s timeout) -> both fail -> error
- **Fix**: Run explicit warmup commands after pulling models (see Step 7b). Each `ollama run` forces the model into memory. Once loaded, models stay resident until the container restarts.
- **Log signature**:
  ```
  ollama_timeout → message_routed (confidence: 0.5, duration_ms: 30003)
  ollama_generation_failed → intent_handled (response_length: 56)
  ```
- **Note**: The bot's built-in keep-warm task pings models every 5 minutes, but this only works if the model is already loaded. It cannot trigger the initial disk-to-RAM load within the timeout window.

### Issue 8: Router Container Memory Limit Too Low (Ollama OOM Stall)

- **Symptom**: Bot timeouts persist even after models are pulled and warmed up via `docker exec`. Router model appears loaded (`ollama ps` shows it), but generate requests from other containers hang forever. `docker stats` shows router at 99.97% memory.
- **Cause**: The `docker-compose.yml` had `memory: 1G` for the ollama-router container, but llama3.2:3b at Q8_0 quantization requires 2.5 GB just for model weights. With only 1GB available, the container had zero headroom for inference working memory. Ollama could hold the model in memory but couldn't allocate the additional buffers needed to actually run inference, causing requests to stall.
- **Key diagnostic**: `docker exec ollama run` (runs inside the container) appeared to work because Ollama handles internal requests differently, but HTTP API requests from other containers over the Docker network (`http://ollama-router:11434/api/generate`) hung indefinitely. The `/api/tags` endpoint (metadata only, no inference) also worked fine, making this look like a networking issue when it was actually a memory issue.
- **Fix**: Increased router memory limit in `docker-compose.yml`:

  ```yaml
  # Before (broken):
  memory: 1G  # 0.5b model only needs ~500MB  ← wrong comment, wrong limit

  # After (working):
  memory: 3G  # 3b model needs ~2.5GB + inference overhead
  ```

  After fix: router uses 1.5 GiB / 3 GiB (50%) and responds in <1 second.
- **Rule of thumb**: Set Ollama container memory limit to at least **2x the model size** to ensure adequate inference headroom.

---

## Final State

| Service | Container | Status | Notes |
|---------|-----------|--------|-------|
| Bot | zetherion-ai-bot | healthy | Connected to Discord as SecureClaw#7693 |
| Skills | zetherion-ai-skills | healthy | HTTP service on port 8080 (internal) |
| Qdrant | zetherion-ai-qdrant | healthy | Vector DB on port 6333 |
| PostgreSQL | zetherion-ai-postgres | healthy | Relational DB on port 5432 |
| Ollama (Generation) | zetherion-ai-ollama | healthy | llama3.1:8b + nomic-embed-text |
| Ollama (Router) | zetherion-ai-ollama-router | healthy | llama3.2:3b |

### Ollama Models Installed
| Container | Model | Size |
|-----------|-------|------|
| ollama (generation) | llama3.1:8b | 4.9 GB |
| ollama (generation) | nomic-embed-text | 274 MB |
| ollama-router | llama3.2:3b | 2.0 GB |

---

## Key Learnings for Automation

1. **SSH to Windows PowerShell works well** for remote deployment, but requires careful handling of:
   - PowerShell syntax differences (`;` not `&&`, different string escaping)
   - Credential store limitations (Windows Credential Manager unavailable over SSH)
   - stderr handling (PowerShell treats stderr as errors, causing misleading exit codes)

2. **Docker Desktop must be started before SSH deployment** - it doesn't auto-start on Windows like a daemon

3. **Model pulls are the slowest part** (~6.5GB total) - consider pre-pulling or caching

4. **The `.env` file can be SCP'd** directly from an existing installation, simplifying key management

5. **Network profile matters** - Windows Public network blocks all inbound connections; must be set to Private for SSH access

6. **Container memory limits must account for inference overhead** - Ollama needs ~2x the model file size in RAM (model weights + inference buffers). A 2.0GB model needs ~3GB container limit. Insufficient memory causes silent stalls where the API accepts connections but never returns responses.

7. **Always run end-to-end verification** (Step 10) after installation. Testing from inside the Docker network catches issues that `docker exec` tests miss, since `docker exec` runs inside the container (localhost) while the bot communicates over the Docker bridge network.
