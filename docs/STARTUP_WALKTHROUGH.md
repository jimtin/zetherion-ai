# Zetherion AI Startup Script Walkthrough

A comprehensive guide to understanding the `start.sh` script - what it does, why, and how it handles errors.

## Table of Contents

1. [Overview](#overview)
2. [Execution Flow Diagram](#execution-flow-diagram)
3. [Phase-by-Phase Breakdown](#phase-by-phase-breakdown)
4. [Decision Trees](#decision-trees)
5. [Timing Expectations](#timing-expectations)
6. [Error Handling](#error-handling)
7. [Environment Variables](#environment-variables)

---

## Overview

The `start.sh` script is the **single entry point** for running Zetherion AI. It handles:

- ✅ Dependency verification (Python, Docker)
- ✅ Environment configuration (.env file)
- ✅ Virtual environment management
- ✅ Docker container orchestration
- ✅ Ollama model download and memory management
- ✅ Automatic Docker Desktop management

**Design Philosophy:**
- **Zero-knowledge startup**: Works for first-time users with minimal configuration
- **Idempotent**: Safe to run multiple times - won't duplicate work
- **Fail-fast**: Exits immediately on critical errors with clear guidance
- **Progressive enhancement**: Detects capabilities and offers upgrades

**Typical runtime:**
- First run with Ollama: **5-10 minutes** (model download)
- First run with Gemini: **30-60 seconds** (Docker startup)
- Subsequent runs: **10-20 seconds** (containers already exist)

---

## Execution Flow Diagram

```
┌──────────────────────────────────────────────────────┐
│ START: ./start.sh                                     │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 1: Environment Validation                       │
│  • Check Python 3.12+                                 │
│  • Check Docker installed                             │
│  • Check Docker daemon ready                          │
│  • Launch Docker if needed                            │
│  • Check .env file exists                             │
│  • Validate required vars (DISCORD_TOKEN, GEMINI_KEY) │
│  Duration: 5-90 seconds (if launching Docker)         │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 2: Router Backend Selection (if not set)       │
│  • Prompt user: Gemini or Ollama?                     │
│  • Save choice to .env                                │
│  Duration: 5-10 seconds (user interaction)            │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 3: Python Environment                           │
│  • Create/activate virtual environment                │
│  • Install dependencies if missing                    │
│  Duration: 5-60 seconds (depends on cache)            │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 4: Qdrant Vector Database                       │
│  • Check if container exists                          │
│  • Create or start container                          │
│  • Wait for health check (30s max)                    │
│  Duration: 5-15 seconds                               │
└───────────────────┬──────────────────────────────────┘
                    │
                    │      ┌─────────────────────┐
                    ├──────► ROUTER_BACKEND=gemini?
                    │      └──────┬──────────────┘
                    │             │ Yes
                    │             ▼
                    │      ┌─────────────────────┐
                    │      │ Skip Ollama phases  │
                    │      │ Go to Phase 8       │
                    │      └──────┬──────────────┘
                    │             │
                    │ No (Ollama) │
                    ▼             │
┌───────────────────┴─────────────▼────────────────────┐
│ Phase 5: Ollama System Assessment (Ollama only)       │
│  • Check if already assessed (.ollama_assessed)       │
│  • Run hardware detection                             │
│  • Recommend model based on RAM/CPU/GPU               │
│  • Update .env with OLLAMA_ROUTER_MODEL & DOCKER_MEM  │
│  Duration: 10-20 seconds (user interaction)           │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 6: Docker Memory Check (Ollama only)            │
│  • Read OLLAMA_DOCKER_MEMORY from .env               │
│  • Check Docker Desktop total memory                  │
│  • If insufficient, prompt user:                      │
│    1. Auto-increase (calls increase-docker-memory.sh) │
│    2. Choose smaller model (exit, re-run)            │
│    3. Continue anyway (risky)                         │
│  Duration: 30-90 seconds (if increasing memory)       │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 7: Ollama Container & Model (Ollama only)       │
│  • Create/start Ollama container                      │
│  • Wait for API ready (30s max)                       │
│  • Check if model downloaded                          │
│  • Pull model if missing (~4.7GB, 3-7 minutes)        │
│  Duration: 10 seconds - 10 minutes (first run)        │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 8: Configuration Summary                        │
│  • Display all settings                               │
│  • Show API keys (truncated)                          │
│  • Show backend choice                                │
│  Duration: 1 second                                   │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│ Phase 9: Start Bot                                    │
│  • Set PYTHONPATH                                     │
│  • Launch: python -m secureclaw                       │
│  • Run until Ctrl+C                                   │
└──────────────────────────────────────────────────────┘
```

---

## Phase-by-Phase Breakdown

### Phase 1: Environment Validation

**Purpose:** Ensure all prerequisites are met before continuing.

#### 1.1 Python Version Check (Lines 43-65)

```bash
# Check for Python 3.12 or 3.13 explicitly
if command_exists python3.12; then
    PYTHON_CMD="python3.12"
elif command_exists python3.13; then
    PYTHON_CMD="python3.13"
elif command_exists python3; then
    # Fallback: check if python3 is >= 3.12
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
    if [[ $(echo "$PYTHON_VERSION >= 3.12" | bc -l) -eq 1 ]]; then
        PYTHON_CMD="python3"
    else
        exit 1  # Version too old
    fi
else
    exit 1  # Not found
fi
```

**Decision Logic:**
- Prefer explicit versions (python3.12, python3.13)
- Fall back to generic python3 if >= 3.12
- Exit with error if none found

**Why 3.12+?** Zetherion AI uses modern Python features (type hints, pattern matching) requiring 3.12+.

**Error Example:**
```
✗ Python 3.12+ required, found 3.11
ℹ Install with: brew install python@3.12
```

#### 1.2 Docker Check & Auto-Launch (Lines 67-157)

**Step 1: Check if Docker CLI exists**
```bash
if ! command_exists docker; then
    print_error "Docker not found"
    exit 1
fi
```

**Step 2: Check if daemon is ready**
```bash
if ! docker info >/dev/null 2>&1; then
    # Daemon not ready - check if Docker Desktop is starting
```

**Step 3: Determine Docker state**
```
┌─────────────────────────────────────────┐
│ Is Docker daemon ready?                 │
│ (docker info succeeds)                  │
└──────────┬─────────────┬────────────────┘
           │ Yes         │ No
           ▼             ▼
    ┌──────────┐   ┌────────────────────┐
    │ Continue │   │ Is Docker Desktop  │
    │          │   │ process running?   │
    │          │   │ (pgrep -x "Docker")│
    └──────────┘   └──────┬─────────────┘
                          │
                ┌─────────┴─────────┐
                │ Yes               │ No
                ▼                   ▼
        ┌───────────────┐   ┌─────────────────┐
        │ Starting...   │   │ Launch Docker   │
        │ Wait 90s max  │   │ Desktop         │
        └───────────────┘   │ Wait for daemon │
                            └─────────────────┘
```

**Step 4: Launch Docker if needed**
```bash
# Verify Docker.app exists
if [ ! -d "/Applications/Docker.app" ]; then
    print_error "Docker Desktop not found at /Applications/Docker.app"
    exit 1
fi

# Launch
open -a Docker

# Initial wait (5 seconds) for process to spawn
sleep 5

# Quick check loop (4 attempts x 5 seconds = 20s total)
for attempt in {1..4}; do
    if docker info >/dev/null 2>&1; then
        print_success "Docker daemon is ready"
        break 2  # Success! Exit both loops
    fi
    sleep 5
done
```

**Two-Phase Wait Strategy:**

1. **Quick Phase (20 seconds):** 4 attempts with 5-second intervals
   - Optimized for fast machines / warm starts
   - Most machines ready in 10-20 seconds

2. **Extended Phase (90 seconds):** Continues if quick phase fails
   - Handles slow machines / cold starts
   - Shows progress every 10 seconds

**Why this approach?**
- Fast machines don't wait unnecessarily
- Slow machines get enough time
- Clear progress feedback to user

**Timing Breakdown:**
```
Fast machine (Docker already warm):
  Launch: 2s + Quick phase: 5-10s = 7-12 seconds total

Slow machine (Docker cold start):
  Launch: 2s + Quick phase: 20s + Extended: 30s = 52 seconds total

Very slow machine:
  Launch: 2s + Quick phase: 20s + Extended: 60s = 82 seconds total
```

#### 1.3 .env File Validation (Lines 159-183)

**Check file exists:**
```bash
if [ ! -f .env ]; then
    print_error ".env file not found"
    print_info "Copy .env.example to .env and add your API keys"
    exit 1
fi
```

**Validate required variables:**
```bash
source .env  # Load variables

MISSING_VARS=()
if [ -z "$DISCORD_TOKEN" ]; then
    MISSING_VARS+=("DISCORD_TOKEN")
fi
if [ -z "$GEMINI_API_KEY" ]; then
    MISSING_VARS+=("GEMINI_API_KEY")
fi

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    print_error "Missing required environment variables: ${MISSING_VARS[*]}"
    exit 1
fi
```

**Why source early?** We need env vars for subsequent phases (router selection, Ollama config).

---

### Phase 2: Router Backend Selection

**Purpose:** One-time choice between Gemini (cloud) and Ollama (local) routing.

#### Trigger Condition (Line 186)

```bash
if [ -z "$ROUTER_BACKEND" ]; then
    # Not set in .env - ask user
```

**When does this happen?**
- First run (fresh .env from .env.example)
- User manually removed ROUTER_BACKEND from .env

**Interactive Prompt (Lines 187-225):**

```
═══════════════════════════════════════════════
  Router Backend Selection
═══════════════════════════════════════════════

Zetherion AI can use two different backends for message routing:

  1. Gemini (Google) - Cloud-based, fast, minimal setup
     • Uses your existing Gemini API key
     • No additional downloads
     • Recommended for cloud-based workflows

  2. Ollama (Local) - Privacy-focused, runs on your machine
     • No data sent to external APIs for routing
     • ~5GB model download (first time only)
     • Recommended for privacy-conscious users

Which backend would you like to use? (1=Gemini, 2=Ollama) [1]:
```

**Decision Logic:**
```bash
case "$REPLY" in
    2)
        ROUTER_BACKEND="ollama"
        ;;
    1|"")  # Default to Gemini if user just presses Enter
        ROUTER_BACKEND="gemini"
        ;;
    *)
        print_warning "Invalid selection, defaulting to Gemini"
        ROUTER_BACKEND="gemini"
        ;;
esac

# Save to .env for future runs
echo "ROUTER_BACKEND=$ROUTER_BACKEND" >> .env
```

**Why persist to .env?**
- User only chooses once
- Subsequent runs skip this prompt
- Can be changed by editing .env manually

**Typical Timing:** 5-10 seconds (user reading and choosing)

---

### Phase 3: Python Environment

**Purpose:** Isolate dependencies in a virtual environment.

#### 3.1 Virtual Environment Creation (Lines 228-238)

```bash
if [ ! -d ".venv" ]; then
    print_warning "Virtual environment not found, creating..."
    $PYTHON_CMD -m venv .venv
    print_success "Virtual environment created"
fi

source .venv/bin/activate
```

**Why check first?** Avoid re-creating existing venv (wastes time).

**Timing:** 3-5 seconds to create, <1 second to activate

#### 3.2 Dependency Installation (Lines 240-250)

```bash
if ! python -c "import discord" 2>/dev/null; then
    # discord.py is a core dependency - if it's not installed, nothing is
    print_warning "Dependencies not installed, installing..."
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install -e .
    print_success "Dependencies installed"
else
    print_success "Dependencies already installed"
fi
```

**Optimization:** Test for a single package instead of all packages.
- Fast path: <1 second (dependencies already installed)
- Slow path: 30-60 seconds (first time)

**Why `pip install -e .`?**
- Installs Zetherion AI in "editable" mode
- Code changes apply immediately (no reinstall needed)

---

### Phase 4: Qdrant Vector Database

**Purpose:** Start vector database for conversation memory and embeddings.

#### Container Lifecycle (Lines 252-270)

**State Machine:**
```
┌────────────────────────────────────┐
│ Does container exist?               │
│ (docker ps -a | grep secureclaw-    │
│  qdrant)                           │
└──────┬──────────────┬──────────────┘
       │ Yes          │ No
       ▼              ▼
┌──────────────┐  ┌─────────────────┐
│ Is it        │  │ Create new      │
│ running?     │  │ container:      │
│ (docker ps)  │  │ docker run -d   │
└──┬───────┬───┘  │   --name ...    │
   │ Yes   │ No   │   -p 6333:6333  │
   ▼       ▼      │   -v ...        │
┌────┐  ┌────┐   │   qdrant/qdrant │
│Skip│  │Start│  └─────────────────┘
└────┘  └────┘
```

**Create Command:**
```bash
docker run -d \
    --name secureclaw-qdrant \
    -p 6333:6333 \
    -v "$(pwd)/qdrant_storage:/qdrant/storage" \
    qdrant/qdrant:latest
```

**Why volume mount?** Persist vector embeddings across container restarts.

#### Health Check (Lines 272-287)

```bash
MAX_RETRIES=30  # 30 seconds max
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:6333/healthz >/dev/null 2>&1; then
        print_success "Qdrant is ready"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    sleep 1
done
```

**Why curl healthz?**
- Qdrant exposes a health endpoint
- More reliable than assuming "started = ready"

**Typical Timing:**
- Container already running: 1-2 seconds
- Container starting: 3-5 seconds
- First time (pulling image): 10-30 seconds

---

### Phase 5: Ollama System Assessment (Ollama Only)

**Skip Condition:** If `ROUTER_BACKEND != "ollama"`, phases 5-7 are skipped entirely.

**Purpose:** Detect hardware and recommend optimal Ollama model.

#### Assessment Trigger (Lines 293-294)

```bash
if [ ! -f ".ollama_assessed" ] || [ -z "$OLLAMA_ROUTER_MODEL" ]; then
    # Run assessment
```

**When does this run?**
1. First time using Ollama (`.ollama_assessed` doesn't exist)
2. User removed model from .env (`OLLAMA_ROUTER_MODEL` empty)

**When is it skipped?**
- `.ollama_assessed` marker exists AND `OLLAMA_ROUTER_MODEL` is set
- User can force re-assessment: `rm .ollama_assessed && ./start.sh`

#### Hardware Detection (Lines 295-326)

**Script:** `scripts/assess-system.py`

**What it detects:**
```python
{
    'platform': 'Darwin',        # macOS
    'machine': 'arm64',          # Apple Silicon
    'cpu_count': 10,             # Physical cores
    'cpu_threads': 10,           # Logical cores
    'ram_gb': 16.0,              # Total RAM
    'available_ram_gb': 8.5,     # Available now
    'disk_free_gb': 250.0,       # Free disk space
    'gpu': {
        'type': 'apple_silicon', # or 'nvidia', 'none'
        'name': 'Apple Silicon',
        'unified_memory': True
    }
}
```

**Recommendation Logic:**
```python
if ram < 6:
    return phi3:mini (5GB Docker, 2.3GB model)
elif ram < 12 and gpu == 'none':
    return llama3.1:8b (8GB Docker, 4.7GB model)
elif gpu in ['nvidia', 'apple_silicon']:
    return qwen2.5:7b (10GB Docker, 4.7GB model)  # GPU accelerated
elif ram >= 16:
    return qwen2.5:7b (10GB Docker, 4.7GB model)  # High RAM
else:
    return llama3.1:8b (8GB Docker, 4.7GB model)  # Default
```

**Output:**
```
═══════════════════════════════════════════════
Zetherion AI System Assessment
═══════════════════════════════════════════════

🖥️  HARDWARE DETECTED:
  Platform: Darwin (arm64)
  CPU: 10 cores (10 threads)
  RAM: 16.0 GB total
  RAM Available: 8.5 GB
  Disk Space: 250.0 GB free
  GPU: Apple Silicon

🤖 RECOMMENDED MODEL:
  Model: qwen2.5:7b
  Download Size: 4.7 GB
  RAM Required: 8 GB minimum
  Docker Memory: 10 GB (automatically configured)
  Expected Speed: ~2-3s (CPU), ~500ms (GPU)
  Quality: Best
  Description: Best reasoning quality for routing

💡 RECOMMENDATION REASON:
  GPU detected! You can use higher quality models with fast inference.

Would you like to use the recommended model? (Y/n):
```

**User Interaction:**
```bash
read -p "Would you like to use the recommended model? (Y/n): " -r

if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    # User accepted (or just pressed Enter)
    $PYTHON_CMD scripts/assess-system.py --update-env

    # This updates .env with:
    # OLLAMA_ROUTER_MODEL=qwen2.5:7b
    # OLLAMA_DOCKER_MEMORY=10

    source .env  # Reload to get updated values
    touch .ollama_assessed  # Mark as assessed
fi
```

**Timing:** 5-15 seconds (hardware detection + user reading + interaction)

---

### Phase 6: Docker Memory Check (Ollama Only)

**Purpose:** Ensure Docker Desktop has enough RAM allocated for the selected model.

#### Memory Requirement Calculation (Lines 343-352)

```bash
# Get required memory from .env (set by assess-system.py)
OLLAMA_DOCKER_MEMORY="${OLLAMA_DOCKER_MEMORY:-8}"  # Default 8GB

# Get current Docker allocation
DOCKER_TOTAL_MEMORY=$(docker info 2>/dev/null | grep "Total Memory" | awk '{print $3}')
DOCKER_MEMORY_GB=$(echo "$DOCKER_TOTAL_MEMORY" | sed 's/GiB//')

# Compare
REQUIRED_MEMORY=$OLLAMA_DOCKER_MEMORY
if (( $(echo "$DOCKER_MEMORY_GB < $REQUIRED_MEMORY" | bc -l) )); then
    # Insufficient memory!
```

**Example Scenario:**
```
Selected model: qwen2.5:7b
OLLAMA_DOCKER_MEMORY: 10 (from assess-system.py)
Current Docker allocation: 4GB

Problem: 4GB < 10GB ❌
```

#### User Prompt (Lines 353-409)

```
⚠ Docker has only 4GB allocated
⚠ Your selected model requires 10GB

What would you like to do?
  1. Automatically increase Docker memory to 10GB (recommended)
  2. Choose a smaller model that fits current Docker memory
  3. Continue anyway (may fail)

Enter choice (1/2/3) [1]:
```

**Decision Tree:**
```
                User Choice
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
    Option 1     Option 2     Option 3
  Auto-increase  Smaller    Continue
                  model      anyway
        │            │            │
        ▼            ▼            ▼
┌───────────────┐ ┌──────┐  ┌──────────┐
│ Call increase-│ │ rm   │  │ Continue │
│ docker-memory.│ │ .olla│  │ (risky)  │
│ sh --yes      │ │ ma_  │  └──────────┘
│               │ │ asses│
│ • Backup JSON │ │ sed  │
│ • Update      │ │      │
│   memoryMiB   │ │ Exit │
│ • Restart     │ │ 0    │
│   Docker      │ └──────┘
│ • Wait for    │
│   daemon      │
└───────────────┘
```

**Option 1: Auto-increase (Default)**

**Script:** `scripts/increase-docker-memory.sh --yes`

**What it does:**
1. **Backup settings:**
   ```bash
   cp ~/Library/Group\ Containers/group.com.docker/settings.json \
      ~/Library/Group\ Containers/group.com.docker/settings.json.backup.20260205_143022
   ```

2. **Update JSON:**
   ```python
   import json

   with open(settings_file, 'r') as f:
       settings = json.load(f)

   settings['memoryMiB'] = 10240  # 10GB in MiB

   with open(settings_file, 'w') as f:
       json.dump(settings, f, indent=2)
   ```

3. **Restart Docker:**
   ```bash
   # Try AppleScript first
   osascript -e 'quit app "Docker"'

   # Fallback to killall if osascript fails
   if ! osascript -e 'quit app "Docker"' 2>/dev/null; then
       killall Docker
   fi

   # Wait for full shutdown (up to 20s)
   for i in {1..20}; do
       ! pgrep -x "Docker" && break
       sleep 1
   done

   # Launch
   open -a Docker

   # Wait for daemon (up to 60s)
   for i in {1..60}; do
       docker info >/dev/null 2>&1 && break
       sleep 1
   done
   ```

**Why AppleScript first?**
- More graceful shutdown
- Allows Docker to save state
- Fallback to `killall` if it fails

**Why wait loops with timeouts?**
- Prevent infinite hangs
- Provide clear error messages
- Show progress to user

**Timing:**
- Stop Docker: 5-10 seconds
- Start Docker: 30-60 seconds
- **Total: 35-70 seconds**

**Option 2: Smaller Model**
```bash
rm -f .ollama_assessed  # Remove marker
print_info "Please run ./start.sh again to choose a smaller model"
exit 0
```
- Clean exit
- Next run triggers assessment again
- User can choose `phi3:mini` (5GB) instead

**Option 3: Continue Anyway**
```bash
print_warning "Continuing with insufficient memory. Model may crash."
# Script continues, but Ollama likely to OOM
```
- Not recommended
- Useful for testing or if user knows better
- Model will likely crash with OOM errors

---

### Phase 7: Ollama Container & Model (Ollama Only)

**Purpose:** Start Ollama container and download the selected model.

#### Container Management (Lines 411-431)

**State Machine (same as Qdrant):**
```
Container exists? → Yes → Running? → Yes → Skip
                           │          No  → Start
                    No ───────────────────→ Create
```

**Create Command:**
```bash
docker run -d \
    --name secureclaw-ollama \
    --memory="${OLLAMA_DOCKER_MEMORY}g" \        # e.g., 10g
    --memory-swap="${OLLAMA_DOCKER_MEMORY}g" \  # Prevent swap usage
    -p 11434:11434 \
    -v "$(pwd)/ollama_models:/root/.ollama" \   # Persist models
    ollama/ollama:latest
```

**Key Parameters:**
- `--memory`: Container RAM limit (matches Docker Desktop allocation)
- `--memory-swap`: Same as memory (prevents swapping to disk)
- `-v`: Persist downloaded models across container restarts

**Why volume for models?**
- Models are large (2-5GB each)
- Re-downloading every time wastes time and bandwidth
- Survives `docker rm secureclaw-ollama`

#### API Health Check (Lines 433-448)

```bash
MAX_RETRIES=30
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        break  # Ready!
    fi
    sleep 1
done
```

**Why `/api/tags`?**
- Ollama's "list models" endpoint
- Returns empty list if no models, but proves API is responsive

**Timing:**
- Already running: 1-2 seconds
- Starting: 3-5 seconds
- First time (pulling image): 20-40 seconds

#### Model Download (Lines 450-467)

**Check if model exists:**
```bash
OLLAMA_MODEL="${OLLAMA_ROUTER_MODEL:-llama3.1:8b}"

if docker exec secureclaw-ollama ollama list | grep -q "$OLLAMA_MODEL"; then
    print_success "Model '$OLLAMA_MODEL' already available"
else
    # Not downloaded yet
```

**Pull model:**
```bash
print_warning "Model '$OLLAMA_MODEL' not found, downloading (this may take several minutes)..."
print_info "Model size: ~4.7GB - please be patient..."

if docker exec secureclaw-ollama ollama pull "$OLLAMA_MODEL"; then
    print_success "Model '$OLLAMA_MODEL' downloaded successfully"
else
    print_error "Failed to download model '$OLLAMA_MODEL'"
    print_warning "Continuing anyway - the bot will fall back to Gemini if the model isn't available"
fi
```

**Download Progress Example:**
```
pulling manifest
pulling 8934d96d3f08... 100% ▕████████████████▏ 4.7 GB
pulling 8c17c2ebb0ea... 100% ▕████████████████▏ 7.0 KB
pulling 7c23fb36d801... 100% ▕████████████████▏ 4.8 KB
pulling 2e0493f67d0c... 100% ▕████████████████▏   59 B
pulling fa304d675061... 100% ▕████████████████▏   91 B
pulling 42ba7f8a01dd... 100% ▕████████████████▏  557 B
verifying sha256 digest
writing manifest
success
```

**Timing:**
- Model already downloaded: 1-2 seconds
- Downloading 2.3GB (phi3): 2-4 minutes (fast connection)
- Downloading 4.7GB (llama3.1, qwen2.5): 3-7 minutes

**Graceful Failure:**
- If download fails, script continues
- Bot will use Gemini as fallback
- User can manually pull later: `docker exec secureclaw-ollama ollama pull <model>`

---

### Phase 8: Configuration Summary

**Purpose:** Show user exactly what configuration will be used.

**Display (Lines 473-488):**
```
═══════════════════════════════════════════════
  Starting Zetherion AI Bot
═══════════════════════════════════════════════

ℹ Configuration Summary:
  • Python: Python 3.12.1
  • Discord Token: DISCORD_TOKEN_START...
  • Gemini API: GEMINI_API_KEY_START...
  • Anthropic API: ANTHROPIC_API_KEY_S...
  • OpenAI API: OPENAI_API_KEY_START...
  • Qdrant: http://localhost:6333
  • Router Backend: ollama
  • Ollama: http://localhost:11434 (Model: qwen2.5:7b)
  • File Logging: true (Directory: logs)
  • Allowed Users: 123456789,987654321
```

**Security:** API keys truncated to first 20 characters only.

**Timing:** <1 second

---

### Phase 9: Start Bot

**Final Step (Lines 490-497):**

```bash
print_success "All checks passed! Starting bot..."
echo ""
echo -e "${GREEN}Press Ctrl+C to stop the bot${NC}"
echo ""

# Set PYTHONPATH to include src directory
PYTHONPATH="${PWD}/src:${PYTHONPATH}" python -m secureclaw
```

**Why set PYTHONPATH?**
- Zetherion AI source is in `src/secureclaw/`
- Running as module (`-m secureclaw`) requires it to be importable
- Adding `src/` to path makes this work

**What happens next?**
1. Python loads `src/secureclaw/__main__.py`
2. Initializes logging, settings, memory systems
3. Connects to Discord
4. Bot runs until Ctrl+C

---

## Decision Trees

### Docker Launch Decision Tree

```
┌─────────────────────────────────────┐
│ Does `docker` command exist?        │
└──────┬──────────────┬───────────────┘
       │ Yes          │ No
       ▼              ▼
  ┌────────┐    ┌─────────────────────┐
  │Continue│    │ ERROR: Install      │
  └────────┘    │ Docker Desktop      │
                │ EXIT 1              │
                └─────────────────────┘

┌─────────────────────────────────────┐
│ Is daemon ready? (docker info)      │
└──────┬──────────────┬───────────────┘
       │ Yes          │ No
       ▼              ▼
  ┌────────┐    ┌──────────────────────┐
  │Continue│    │ Is Docker.app running│
  └────────┘    │ (pgrep -x "Docker")? │
                └──────┬──────────┬────┘
                       │ Yes      │ No
                       ▼          ▼
               ┌───────────┐  ┌──────────────┐
               │ Wait for  │  │ Launch Docker│
               │ daemon    │  │ Desktop      │
               │ 90s max   │  │ (open -a)    │
               └───────────┘  └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Wait 5s for  │
                              │ process spawn│
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Quick loop:  │
                              │ 4 x 5s = 20s │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Extended:    │
                              │ 90s total    │
                              └──────┬───────┘
                                     │
                        ┌────────────┴────────────┐
                        │ Ready?                  │
                        ▼                         ▼
                   ┌─────────┐            ┌─────────────┐
                   │Continue │            │ ERROR: Timed│
                   └─────────┘            │ out after   │
                                          │ 90 seconds  │
                                          │ EXIT 1      │
                                          └─────────────┘
```

### Router Backend Selection Tree

```
┌────────────────────────────────────┐
│ Is ROUTER_BACKEND set in .env?     │
└──────┬────────────────┬────────────┘
       │ Yes            │ No
       ▼                ▼
  ┌────────┐      ┌──────────────────┐
  │ Use it │      │ Prompt user:     │
  └────────┘      │ 1=Gemini 2=Ollama│
                  └────────┬─────────┘
                           │
                  ┌────────┴────────┐
                  ▼                 ▼
            ┌──────────┐      ┌──────────┐
            │ User: 2  │      │ User: 1  │
            │ or       │      │ or <Enter│
            │ <invalid>│      │ >        │
            └────┬─────┘      └─────┬────┘
                 │                  │
                 ▼                  ▼
         ┌──────────────┐    ┌─────────────┐
         │ ROUTER_      │    │ ROUTER_     │
         │ BACKEND=     │    │ BACKEND=    │
         │ ollama       │    │ gemini      │
         └──────┬───────┘    └──────┬──────┘
                │                   │
                │                   │
                └───────┬───────────┘
                        │
                        ▼
               ┌────────────────────┐
               │ echo ROUTER_BACKEND│
               │ to .env            │
               │ (persist choice)   │
               └────────────────────┘
```

### Ollama Memory Management Tree

```
┌───────────────────────────────────────┐
│ ROUTER_BACKEND == "ollama"?           │
└──────┬────────────────┬───────────────┘
       │ Yes            │ No
       ▼                ▼
  ┌────────┐      ┌──────────┐
  │Continue│      │ Skip all │
  │ to     │      │ Ollama   │
  │ Ollama │      │ phases   │
  │ phases │      └──────────┘
  └────┬───┘
       │
       ▼
┌───────────────────────────────────────┐
│ Check Docker memory vs requirement    │
│ docker info | grep "Total Memory"     │
└──────┬────────────────┬───────────────┘
       │                │
       │ Sufficient     │ Insufficient
       ▼                ▼
  ┌────────┐      ┌──────────────────────┐
  │Continue│      │ Prompt user:         │
  └────────┘      │ 1. Auto-increase     │
                  │ 2. Smaller model     │
                  │ 3. Continue anyway   │
                  └────────┬─────────────┘
                           │
             ┌─────────────┼─────────────┐
             │             │             │
             ▼             ▼             ▼
        ┌────────┐   ┌─────────┐   ┌────────┐
        │ Call   │   │ rm      │   │Continue│
        │increase│   │.ollama_ │   │(risky) │
        │-docker-│   │assessed │   └────────┘
        │memory  │   │         │
        │.sh     │   │EXIT 0   │
        └───┬────┘   └─────────┘
            │
            ▼
     ┌─────────────┐
     │ Backup JSON │
     │ Update mem  │
     │ Restart     │
     │ Docker      │
     └──────┬──────┘
            │
    ┌───────┴────────┐
    │ Success?       │
    ▼                ▼
┌────────┐      ┌──────────┐
│Continue│      │ ERROR    │
└────────┘      │ Give user│
                │ options  │
                │ EXIT 1   │
                └──────────┘
```

---

## Timing Expectations

### First Run (Ollama, No Containers)

| Phase | Task | Time |
|-------|------|------|
| 1.1 | Python check | 1s |
| 1.2 | Docker launch (cold) | 60s |
| 1.3 | .env validation | 1s |
| 2 | Router selection (user) | 10s |
| 3 | Venv + deps install | 60s |
| 4 | Qdrant pull + start | 30s |
| 5 | System assessment (user) | 15s |
| 6 | Docker memory increase | 60s |
| 7 | Ollama pull + start | 40s |
| 7 | Model download (4.7GB) | 300s |
| 8 | Summary | 1s |
| **TOTAL** | | **~9 minutes** |

### First Run (Gemini, No Containers)

| Phase | Task | Time |
|-------|------|------|
| 1-2 | Validation + router | 72s |
| 3 | Venv + deps | 60s |
| 4 | Qdrant pull + start | 30s |
| 5-7 | (Skipped - Gemini) | 0s |
| 8 | Summary | 1s |
| **TOTAL** | | **~3 minutes** |

### Subsequent Runs (Warm)

| Phase | Task | Time |
|-------|------|------|
| 1 | Validation (Docker warm) | 5s |
| 2 | (Skipped - already set) | 0s |
| 3 | Venv activate | 2s |
| 4 | Qdrant start | 3s |
| 5 | (Skipped - assessed) | 0s |
| 6 | (Skipped - sufficient) | 2s |
| 7 | Ollama start | 5s |
| 8 | Summary | 1s |
| **TOTAL** | | **~18 seconds** |

### Cold Docker Start (Worst Case)

| Phase | Task | Time |
|-------|------|------|
| 1.2 | Docker launch | 90s |
| 4 | Qdrant first start | 10s |
| 7 | Ollama first start | 10s |
| **TOTAL ADDED** | | **+110s** |

---

## Error Handling

### Exit Codes

| Code | Meaning | Example Trigger |
|------|---------|-----------------|
| 0 | Success | Normal completion |
| 1 | Fatal error | Missing dependency, .env invalid, timeout |

### Error Categories

#### 1. Missing Prerequisites
```bash
✗ Python 3.12+ required, found 3.11
ℹ Install with: brew install python@3.12
```
**Action:** Install suggested package, re-run

#### 2. Configuration Errors
```bash
✗ Missing required environment variables: DISCORD_TOKEN GEMINI_API_KEY
ℹ Please add them to your .env file
```
**Action:** Edit .env, add missing keys, re-run

#### 3. Docker Errors
```bash
✗ Docker daemon did not become ready after 90 seconds
ℹ Check Docker Desktop status in menu bar and try again
ℹ You may need to restart Docker Desktop manually
```
**Action:**
- Check Docker Desktop icon in menu bar
- Look for error messages in Docker Desktop GUI
- Try manually restarting: Docker menu → Restart
- Check Console.app for Docker crashes

#### 4. Container Errors
```bash
✗ Qdrant failed to start
```
**Action:**
- Check if port already in use: `lsof -i :6333`
- Check container logs: `docker logs secureclaw-qdrant`
- Remove and retry: `docker rm -f secureclaw-qdrant && ./start.sh`

#### 5. Model Download Errors
```bash
✗ Failed to download model 'qwen2.5:7b'
ℹ You can manually pull it later with: docker exec secureclaw-ollama ollama pull qwen2.5:7b
⚠ Continuing anyway - the bot will fall back to Gemini if the model isn't available
```
**Action:**
- Script continues (non-fatal)
- Bot uses Gemini for routing instead
- Manually pull later when network is better
- Or choose smaller model: `rm .ollama_assessed && ./start.sh`

---

## Environment Variables

### Required (Must be in .env)

| Variable | Example | Purpose |
|----------|---------|---------|
| `DISCORD_TOKEN` | `MTA...` | Discord bot authentication |
| `GEMINI_API_KEY` | `AIza...` | Google Gemini API (embeddings, routing) |

### Optional Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROUTER_BACKEND` | `gemini` | Router: `gemini` or `ollama` |
| `ANTHROPIC_API_KEY` | `None` | Claude for complex tasks |
| `OPENAI_API_KEY` | `None` | GPT for complex tasks |
| `ALLOWED_USER_IDS` | `[]` | Discord user ID allowlist |
| `LOG_TO_FILE` | `true` | Enable file logging |
| `LOG_DIRECTORY` | `logs` | Log file location |

### Ollama-Specific

| Variable | Default | Set By | Purpose |
|----------|---------|--------|---------|
| `OLLAMA_HOST` | `ollama` | `start.sh` | Ollama container host |
| `OLLAMA_PORT` | `11434` | Manual | Ollama API port |
| `OLLAMA_ROUTER_MODEL` | `llama3.1:8b` | `assess-system.py` | Model to use |
| `OLLAMA_DOCKER_MEMORY` | `8` | `assess-system.py` | Docker RAM (GB) |
| `OLLAMA_TIMEOUT` | `30` | Manual | API timeout (seconds) |

### Auto-Configured

| Variable | When Set | By Whom |
|----------|----------|---------|
| `ROUTER_BACKEND` | Phase 2 (first run) | User prompt → `start.sh` |
| `OLLAMA_HOST` | Phase 7 (if `ollama`) | `start.sh` (sets to `localhost`) |
| `OLLAMA_ROUTER_MODEL` | Phase 5 (if accepted) | `assess-system.py --update-env` |
| `OLLAMA_DOCKER_MEMORY` | Phase 5 (if accepted) | `assess-system.py --update-env` |

---

## Appendix: Script Structure

### Helper Functions (Lines 11-39)

```bash
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_header() {
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}
```

**Why standardize output?**
- Consistent UX
- Colored output for clarity
- Easy to scan for errors (red ✗) vs success (green ✓)

### Error Handling Pattern

```bash
set -e  # Exit on any error

# For non-fatal errors:
if ! some_command; then
    print_warning "Command failed, continuing..."
fi

# For fatal errors:
if ! critical_command; then
    print_error "Critical failure"
    print_info "How to fix: ..."
    exit 1
fi
```

---

## Summary

The startup script handles:
- ✅ **Environment validation** (Python, Docker, .env)
- ✅ **User choices** (router backend, model selection)
- ✅ **Dependency management** (venv, pip packages)
- ✅ **Container orchestration** (Qdrant, Ollama)
- ✅ **Docker memory automation** (detect, prompt, increase)
- ✅ **Model downloads** (Ollama pull)
- ✅ **Clear feedback** (colored output, progress messages)
- ✅ **Graceful failures** (timeouts, fallbacks, suggestions)

**First run:** 3-9 minutes (depending on backend and network)

**Subsequent runs:** 10-20 seconds (all containers already exist)

**User interaction points:**
1. Router backend choice (first run)
2. Model recommendation acceptance (Ollama first run)
3. Docker memory increase approval (if needed)

**Exit points:**
- Fatal: Missing Python, Docker, .env keys
- Soft: User chooses smaller model (can re-run immediately)
