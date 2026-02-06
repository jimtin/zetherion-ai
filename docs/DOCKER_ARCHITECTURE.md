# Docker Architecture and Memory Management

This document explains how Docker works on macOS, the distinction between Docker Desktop and containers, and how Zetherion AI automatically manages Docker memory allocation for Ollama models.

## Table of Contents

1. [Docker Desktop vs Docker Containers](#docker-desktop-vs-docker-containers)
2. [Memory Hierarchy](#memory-hierarchy)
3. [Why Docker Desktop Needs to Restart](#why-docker-desktop-needs-to-restart)
4. [Automated Memory Management](#automated-memory-management)
5. [Manual Docker Configuration](#manual-docker-configuration)
6. [Troubleshooting](#troubleshooting)

---

## Docker Desktop vs Docker Containers

### Docker Desktop (The Virtual Machine)

On macOS, Docker runs inside a **lightweight virtual machine (VM)** called Docker Desktop. This is necessary because Docker uses Linux-specific features that don't exist natively on macOS.

**Key points:**
- Docker Desktop is the **host environment** that runs all containers
- It has its own **fixed memory allocation** from your Mac's RAM
- This memory allocation is configured in Docker Desktop settings
- Located at: `~/Library/Group Containers/group.com.docker/settings.json`
- The `memoryMiB` field controls how much RAM the VM gets

**Think of it like this:**
```
┌─────────────────────────────────────┐
│   Your Mac (Physical Hardware)      │
│   Total RAM: e.g., 16GB             │
│                                      │
│  ┌────────────────────────────────┐ │
│  │  Docker Desktop VM             │ │
│  │  Allocated: e.g., 8GB          │ │
│  │                                │ │
│  │  ┌──────────────────────────┐ │ │
│  │  │  Container 1 (Bot)       │ │ │
│  │  │  Limit: 2GB              │ │ │
│  │  └──────────────────────────┘ │ │
│  │                                │ │
│  │  ┌──────────────────────────┐ │ │
│  │  │  Container 2 (Ollama)    │ │ │
│  │  │  Limit: 10GB             │ │ │
│  │  └──────────────────────────┘ │ │
│  │                                │ │
│  │  ┌──────────────────────────┐ │ │
│  │  │  Container 3 (Qdrant)    │ │ │
│  │  │  Limit: 1GB              │ │ │
│  │  └──────────────────────────┘ │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
```

### Docker Containers

**Containers** are isolated processes that run **inside** the Docker Desktop VM.

**Key points:**
- Each container can have a **memory limit** (e.g., `--memory=10g`)
- Container limits **cannot exceed** the Docker Desktop VM's total allocation
- If Docker Desktop has 8GB, you cannot give a container 10GB
- Multiple containers **share** the Docker Desktop VM's memory pool

### The Critical Difference

**Setting a container's memory limit does NOT increase Docker Desktop's memory allocation.**

If you run:
```bash
docker run --memory=10g ollama/ollama
```

But Docker Desktop only has 4GB allocated, the container will:
- Be limited to 4GB (the VM's total)
- Or fail to start if it requires more than available
- **Not** automatically increase Docker Desktop's allocation

**That's why we need to restart Docker Desktop** - to resize the VM itself.

---

## Memory Hierarchy

Understanding the three-layer memory hierarchy is crucial:

### Layer 1: Physical RAM
- Your Mac's actual memory (e.g., 16GB, 32GB)
- Shared between macOS and all applications
- Fixed amount, cannot be changed without hardware upgrade

### Layer 2: Docker Desktop VM Allocation
- A **portion** of physical RAM reserved for Docker
- Configured in Docker Desktop settings
- **Requires Docker Desktop restart** to change
- Default: 2-4GB (too small for Ollama models)

### Layer 3: Container Memory Limits
- Individual limits for each container
- Set via `docker run --memory=X` or `docker-compose.yml`
- **Cannot exceed Layer 2** (Docker Desktop VM allocation)
- Can be changed without restarting Docker Desktop

### Example Scenario

**System:** MacBook Pro with 16GB RAM

**Problem:**
- Docker Desktop allocated: 4GB (Layer 2)
- Ollama model needs: 10GB (Layer 3 requirement)

**Why it fails:**
```
Layer 1 (Physical):    16GB  ✓ Enough
Layer 2 (Docker VM):    4GB  ✗ NOT enough
Layer 3 (Container):   10GB  ✗ CANNOT allocate (exceeds Layer 2)
```

**Solution:**
1. Increase Layer 2 (Docker Desktop) to 12GB
2. Restart Docker Desktop to apply
3. Now Layer 3 (container) can use 10GB

---

## Why Docker Desktop Needs to Restart

### The Technical Reason

Docker Desktop's VM allocation is a **boot-time parameter**. The hypervisor (the software that creates the VM) needs to:

1. **Allocate physical pages** in your Mac's RAM
2. **Initialize the virtual memory space** for the VM
3. **Configure the hypervisor** with new limits

These operations **cannot be done while the VM is running**.

### The Analogy

Think of it like upgrading RAM in a desktop computer:
- You can't add more RAM while the computer is running
- You must shut down, install RAM, then boot up
- The BIOS needs to detect and configure the new RAM

Docker Desktop is similar:
- Can't change VM memory while running
- Must quit Docker Desktop
- Restart with new memory allocation

### What About `--memory` Flags?

Container memory limits (`docker run --memory=X`) are different:
- They're enforced by **cgroups** (Linux control groups)
- Can be changed at runtime
- But they're just **soft limits** within the VM's total allocation
- Like dividing a pie - you can't create more pie, just slice it differently

---

## Automated Memory Management

Zetherion AI includes a fully automated pipeline to handle Docker memory requirements for Ollama models.

### The Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│  1. Hardware Detection (scripts/assess-system.py)           │
│     - Detect CPU, RAM, GPU                                  │
│     - Recommend appropriate Ollama model                     │
│     - Calculate required Docker memory                       │
│     - Save to .env: OLLAMA_ROUTER_MODEL, OLLAMA_DOCKER_MEMORY│
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Startup Check (start.sh)                                │
│     - Read required memory from .env                         │
│     - Check current Docker Desktop allocation               │
│     - If insufficient, prompt user                           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  3. User Choice                                             │
│     • Automatically increase (default)                       │
│     • Choose smaller model                                   │
│     • Continue anyway (not recommended)                      │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼ (if "automatically increase")
┌─────────────────────────────────────────────────────────────┐
│  4. Memory Increase (scripts/increase-docker-memory.sh)     │
│     - Backup Docker settings JSON                            │
│     - Update memoryMiB field                                │
│     - Stop Docker Desktop (osascript or killall)            │
│     - Wait for full shutdown (up to 20 seconds)             │
│     - Launch Docker Desktop                                  │
│     - Wait for daemon readiness (up to 60 seconds)          │
└─────────────────────────────────────────────────────────────┘
```

### Model Recommendations with Memory Requirements

The system recommends models based on your hardware:

| Model         | Size  | RAM Req | Docker Memory | Speed (CPU)   | Quality    |
|---------------|-------|---------|---------------|---------------|------------|
| phi3:mini     | 2.3GB | 4GB     | 5GB           | ~1s           | Basic      |
| llama3.1:8b   | 4.7GB | 8GB     | 8GB           | ~2-3s         | Excellent  |
| qwen2.5:7b    | 4.7GB | 8GB     | 10GB          | ~2-3s         | Best       |
| mistral:7b    | 4.1GB | 8GB     | 7GB           | ~1-2s         | Very Good  |

**Docker Memory = Model Size + Overhead**
- Phi3: 2.3GB + 2GB overhead = 5GB
- Llama3.1: 4.7GB + 3GB overhead = 8GB
- Qwen2.5: 4.7GB + 5GB overhead = 10GB (needs more for quality)
- Mistral: 4.1GB + 3GB overhead = 7GB

### Environment Variables

The system manages these automatically:

```bash
# Set by assess-system.py
OLLAMA_ROUTER_MODEL=llama3.1:8b
OLLAMA_DOCKER_MEMORY=8  # in GB

# Used by start.sh
REQUIRED_MEMORY=${OLLAMA_DOCKER_MEMORY:-8}

# Used by Docker Compose
OLLAMA_DOCKER_MEMORY=8  # for container limits
```

### Process Control

The memory increase script uses robust process management:

1. **Check if Docker is running**
   ```bash
   pgrep -x "Docker" > /dev/null
   ```

2. **Stop Docker Desktop**
   - Try AppleScript first: `osascript -e 'quit app "Docker"'`
   - Fallback to killall: `killall Docker`
   - Wait for full stop (up to 20 seconds)

3. **Verify shutdown**
   - Loop checking `pgrep` until process gone
   - Timeout protection to prevent infinite wait

4. **Start Docker Desktop**
   ```bash
   open -a Docker
   ```

5. **Wait for daemon readiness**
   - Not just GUI launch - wait for daemon to respond
   - Use `docker info` to verify daemon is ready
   - Can take 30-60 seconds on cold start
   - Timeout after 60 seconds with helpful error message

---

## Manual Docker Configuration

If you prefer to configure Docker Desktop manually:

### Via Docker Desktop GUI

1. Open Docker Desktop
2. Go to **Settings** (gear icon)
3. Navigate to **Resources** → **Advanced**
4. Adjust the **Memory** slider to desired amount
5. Click **Apply & Restart**
6. Wait for Docker Desktop to restart (30-60 seconds)

### Via Settings File (Advanced)

**Location:** `~/Library/Group Containers/group.com.docker/settings.json`

1. **Backup the file first:**
   ```bash
   cp ~/Library/Group\ Containers/group.com.docker/settings.json \
      ~/Library/Group\ Containers/group.com.docker/settings.json.backup
   ```

2. **Edit the file:**
   ```bash
   vim ~/Library/Group\ Containers/group.com.docker/settings.json
   ```

3. **Find and update memoryMiB:**
   ```json
   {
     "memoryMiB": 10240,  // 10GB in MiB
     ...
   }
   ```

4. **Restart Docker Desktop:**
   ```bash
   osascript -e 'quit app "Docker"'
   sleep 5
   open -a Docker
   ```

### Verify the Change

```bash
# Check Docker's total memory
docker info | grep "Total Memory"

# Should show: Total Memory: 10 GiB (or your configured amount)
```

---

## Troubleshooting

### Docker Desktop Won't Start After Increasing Memory

**Symptom:** Docker Desktop GUI opens but daemon never becomes ready

**Possible Causes:**
1. Requested memory exceeds available system RAM
2. Other apps consuming too much memory
3. Docker Desktop settings corrupted

**Solutions:**

1. **Check available RAM:**
   ```bash
   # macOS
   vm_stat | head -2
   ```

2. **Reduce requested memory:**
   - Restore backup settings:
     ```bash
     cp ~/Library/Group\ Containers/group.com.docker/settings.json.backup \
        ~/Library/Group\ Containers/group.com.docker/settings.json
     ```
   - Restart Docker Desktop

3. **Reset Docker Desktop:**
   - Docker Desktop → Troubleshoot → Reset to factory defaults
   - **WARNING:** This deletes all containers and images

### Container Fails with "Out of Memory"

**Symptom:** Container starts but crashes with OOM errors

**Check:**
1. **Docker Desktop allocation:**
   ```bash
   docker info | grep "Total Memory"
   ```

2. **Container limit:**
   ```bash
   docker inspect CONTAINER_ID | grep Memory
   ```

3. **Model requirements:**
   - Check `OLLAMA_DOCKER_MEMORY` in `.env`
   - Verify it matches model needs (see table above)

**Fix:**
```bash
# Run the automated increase script
cd scripts
./increase-docker-memory.sh --yes

# Or manually increase Docker Desktop memory via GUI
```

### "Unable to find image 'ollama/ollama:latest'"

**Symptom:** Ollama container fails to start

**Cause:** Image not pulled yet (expected on first run)

**Fix:**
```bash
# The startup script handles this, but manual pull:
docker pull ollama/ollama:latest
```

### Docker Daemon Not Responding After Start

**Symptom:** `docker info` returns error after launching Docker Desktop

**Cause:** Daemon still initializing (can take 30-60 seconds)

**Check:**
```bash
# Wait and retry
for i in {1..60}; do
    docker info >/dev/null 2>&1 && echo "Ready!" && break
    echo "Waiting... ($i/60)"
    sleep 1
done
```

**If still fails after 60 seconds:**
1. Check Console.app for Docker errors
2. Check Activity Monitor for Docker processes
3. Try restarting Mac (last resort)

### Automated Script Hangs

**Symptom:** `increase-docker-memory.sh` hangs during Docker restart

**Debug:**
```bash
# Run with manual prompts to see progress
cd scripts
./increase-docker-memory.sh  # without --yes flag

# Check Docker process
pgrep -x "Docker"

# Check daemon
docker info
```

**Common causes:**
1. Docker.app not installed at `/Applications/Docker.app`
2. Permissions issues with settings file
3. Docker Desktop GUI stuck in update/crash loop

**Solutions:**
1. **Verify Docker.app location:**
   ```bash
   ls -la /Applications/Docker.app
   ```

2. **Check settings file permissions:**
   ```bash
   ls -la ~/Library/Group\ Containers/group.com.docker/settings.json
   ```

3. **Force quit all Docker processes:**
   ```bash
   pkill -9 -x "Docker"
   pkill -9 -f "com.docker"
   sleep 3
   open -a Docker
   ```

---

## Advanced Topics

### Docker Desktop Architecture on macOS

Docker Desktop uses **HyperKit** (a lightweight hypervisor) to run a minimal Linux VM:

1. **HyperKit** creates the VM
2. **LinuxKit** provides the minimal Linux environment
3. **containerd** manages container lifecycle
4. **Docker daemon** provides the API

Your containers run inside the LinuxKit VM, not directly on macOS.

### Memory Management Internals

**At the hypervisor level:**
- HyperKit allocates physical RAM pages from macOS
- Creates guest physical memory space for Linux VM
- Cannot dynamically resize without restart

**At the Linux level (inside VM):**
- cgroups enforce container memory limits
- OOM killer terminates processes exceeding limits
- Can be adjusted without restarting VM

### Performance Considerations

**Docker Desktop allocation:**
- Too little: Containers OOM, poor performance
- Too much: Less RAM for macOS, potential swapping
- Sweet spot: 50-70% of total RAM for Docker

**Example for 16GB Mac:**
- Docker: 10GB (Ollama + other services)
- macOS: 6GB (system + apps)

**With 8GB Mac:**
- Docker: 5GB (smaller Ollama model)
- macOS: 3GB (minimal)
- May need to close other apps

---

## References

- [Docker Desktop for Mac documentation](https://docs.docker.com/desktop/settings/mac/)
- [HyperKit GitHub](https://github.com/moby/hyperkit)
- [LinuxKit GitHub](https://github.com/linuxkit/linuxkit)
- [Docker Memory Limits](https://docs.docker.com/config/containers/resource_constraints/#memory)
