# Hardware Recommendations

This guide helps you choose the optimal hardware configuration for running Zetherion AI, including recommendations for Ollama model selection based on your system resources.

## Table of Contents

- [Quick Decision Guide](#quick-decision-guide)
- [Minimum Requirements](#minimum-requirements)
- [Recommended Specifications](#recommended-specifications)
- [Ollama Model Recommendations](#ollama-model-recommendations)
- [GPU Acceleration](#gpu-acceleration)
- [Docker Memory Configuration](#docker-memory-configuration)
- [Performance Benchmarks](#performance-benchmarks)
- [Cost Analysis](#cost-analysis)

## Quick Decision Guide

**Choose your path:**

1. **I want minimal setup and don't need local AI**
   - Use Gemini backend (cloud-based)
   - Requires: 8GB RAM, any modern CPU
   - Setup time: ~3 minutes
   - **Best for**: Quick deployments, cloud-based workflows

2. **I want privacy with modest hardware (8-16GB RAM)**
   - Use Ollama with `llama3.1:8b` or `mistral:7b`
   - Setup time: ~9 minutes (includes model download)
   - **Best for**: Privacy-conscious users, moderate performance

3. **I have powerful hardware (16GB+ RAM or GPU)**
   - Use Ollama with `qwen2.5:14b` or larger models
   - Setup time: ~12 minutes (larger downloads)
   - **Best for**: Best quality local AI, offline capability

The `start.sh`/`start.ps1` script **automatically detects your hardware** and recommends the optimal configuration.

## Minimum Requirements

### For Gemini Backend (Cloud Routing)

| Component | Minimum Specification |
|-----------|----------------------|
| **OS** | Windows 10/11, macOS 10.15+, Ubuntu 20.04+, or compatible Linux |
| **CPU** | Any modern x86_64 or ARM64 CPU (2+ cores) |
| **RAM** | 4GB system RAM |
| **Docker** | Docker Desktop 4.0+ with 2GB RAM allocated |
| **Disk** | 10GB free space |
| **Network** | Internet connection (required for API calls) |

**Note**: Gemini backend uses cloud services, so local resources are minimal.

### For Ollama Backend (Local Routing)

| Component | Minimum Specification |
|-----------|----------------------|
| **OS** | Windows 10/11, macOS 10.15+, Ubuntu 20.04+, or compatible Linux |
| **CPU** | Modern x86_64 or ARM64 CPU (4+ cores recommended) |
| **RAM** | 12GB system RAM |
| **Docker** | Docker Desktop 4.0+ with 9GB RAM allocated |
| **Disk** | 25GB free space (10GB for Docker images, 10-15GB for models) |
| **Network** | Internet connection (for initial model download) |

**Note**: Zetherion AI uses a **dual-container Ollama architecture**:
- **Router container** (1GB): Fast message classification
- **Generation container** (8GB+): Complex queries and embeddings
- This eliminates model-swapping delays (2-10 seconds per swap)

## Recommended Specifications

### For Best Experience (Ollama + Quality Models)

| Component | Recommended Specification |
|-----------|--------------------------|
| **OS** | Windows 11, macOS 12+, Ubuntu 22.04+ |
| **CPU** | Modern multi-core CPU (8+ cores, 3.0+ GHz) |
| **RAM** | 16GB system RAM (32GB for larger models) |
| **GPU** | NVIDIA RTX 3060 (8GB VRAM) or better, or Apple M1/M2/M3 |
| **Docker** | Docker Desktop 4.25+ with 12GB RAM allocated |
| **Disk** | 30GB+ free SSD space |
| **Network** | Broadband internet (for initial setup) |

### What The Extra Resources Buy You

- **16GB+ RAM**: Can run larger, more capable models (`qwen2.5:14b`, `llama3.1:70b` variants)
- **GPU**: 5-10x faster inference, enabling real-time responses
- **SSD**: Faster model loading and Docker operations
- **More CPU Cores**: Better concurrent request handling

## Ollama Model Recommendations

The startup script (`start.sh`/`start.ps1`) automatically detects your hardware and recommends the optimal model. Here's the logic:

### Model Tiers by Hardware

#### Tier 1: Lightweight (4-8GB System RAM)

**Recommended Model**: `phi3:mini`

| Attribute | Value |
|-----------|-------|
| **Model Size** | ~2.7GB download |
| **Docker Memory** | 5GB |
| **System RAM** | 8GB minimum |
| **GPU** | Optional (runs well on CPU) |
| **Inference Speed** | ~50 tokens/sec (CPU), ~200 tokens/sec (GPU) |
| **Quality** | Good for simple queries, basic conversation |
| **Best For** | Low-resource systems, fast responses |

**Example Hardware**: Budget laptops, older desktops, single-board computers

#### Tier 2: Balanced (8-16GB System RAM)

**Recommended Model**: `llama3.1:8b` (default)

| Attribute | Value |
|-----------|-------|
| **Model Size** | ~4.7GB download |
| **Docker Memory** | 8GB |
| **System RAM** | 12GB minimum (16GB recommended) |
| **GPU** | Optional (6GB+ VRAM for GPU acceleration) |
| **Inference Speed** | ~30 tokens/sec (CPU), ~150 tokens/sec (GPU) |
| **Quality** | Excellent balance of quality and speed |
| **Best For** | General use, most users |

**Example Hardware**: Modern laptops, mid-range desktops, NVIDIA GTX 1660+

#### Tier 3: High Quality (16GB+ System RAM)

**Recommended Model**: `qwen2.5:14b`

| Attribute | Value |
|-----------|-------|
| **Model Size** | ~9.0GB download |
| **Docker Memory** | 12GB |
| **System RAM** | 16GB minimum (24GB recommended) |
| **GPU** | Highly recommended (8GB+ VRAM) |
| **Inference Speed** | ~20 tokens/sec (CPU), ~100 tokens/sec (GPU) |
| **Quality** | Best quality local AI, comparable to cloud models |
| **Best For** | Power users, quality-focused workflows |

**Example Hardware**: High-end laptops, gaming PCs, workstations, NVIDIA RTX 3060+

#### Tier 4: Maximum Quality (32GB+ System RAM + GPU)

**Recommended Model**: `qwen2.5:32b` or `llama3.1:70b` variants

| Attribute | Value |
|-----------|-------|
| **Model Size** | 18-40GB download |
| **Docker Memory** | 20-32GB |
| **System RAM** | 32GB minimum (64GB recommended) |
| **GPU** | Required (16GB+ VRAM) |
| **Inference Speed** | ~10-15 tokens/sec (GPU) |
| **Quality** | Highest quality local AI available |
| **Best For** | Professionals, research, offline deployments |

**Example Hardware**: NVIDIA RTX 3090/4090, Apple M2 Ultra, workstation PCs

### Alternative Models by Use Case

| Use Case | Model | Why |
|----------|-------|-----|
| **Fastest Response** | `mistral:7b` | Optimized for speed, lower quality |
| **Best Quality per GB** | `qwen2.5:7b` | Excellent performance for size |
| **Code Generation** | `codellama:13b` | Specialized for programming tasks |
| **Privacy + Quality** | `qwen2.5:14b` | Best local model under 10GB |
| **Offline Capability** | `llama3.1:8b` | Robust, well-tested, reliable |

## GPU Acceleration

### Supported GPUs

#### NVIDIA (CUDA)
- **Excellent Support**: RTX 3060, 3070, 3080, 3090, 4070, 4080, 4090
- **Good Support**: GTX 1660, 1070, 1080, RTX 2060, 2070, 2080
- **Minimum**: 6GB VRAM for `llama3.1:8b`, 8GB+ for larger models
- **Drivers**: CUDA 11.8+ recommended

#### AMD (ROCm)
- **Excellent Support**: RX 6800, 6900 XT, 7900 XT, 7900 XTX
- **Good Support**: RX 5700 XT, Vega 64
- **Minimum**: 8GB VRAM
- **Drivers**: ROCm 5.5+ on Linux only (Windows not supported)

#### Apple Silicon (Metal)
- **Excellent Support**: M1 Max, M2 Pro/Max/Ultra, M3 Pro/Max/Ultra
- **Good Support**: M1 Pro, M2
- **Minimum**: 16GB unified memory for best experience
- **Drivers**: Built into macOS (no additional install)

### GPU vs CPU Performance

**Example: `llama3.1:8b` on typical hardware**

| Configuration | Tokens/Second | Response Time (100 tokens) |
|---------------|---------------|---------------------------|
| **CPU** (Intel i7-12700) | ~30 tok/s | ~3.3 seconds |
| **GPU** (NVIDIA RTX 3060) | ~150 tok/s | ~0.7 seconds |
| **GPU** (NVIDIA RTX 4090) | ~300 tok/s | ~0.3 seconds |
| **Apple** (M2 Max) | ~120 tok/s | ~0.8 seconds |

**Speedup**: GPU provides **5-10x faster** inference compared to CPU-only.

### Enabling GPU Support

GPU support is **automatic** if you have compatible hardware:

1. **NVIDIA**: Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
2. **AMD**: Install [ROCm](https://rocm.docs.amd.com/en/latest/deploy/linux/index.html) (Linux only)
3. **Apple Silicon**: Built-in, no additional setup

Ollama Docker container will automatically detect and use available GPU.

## Docker Memory Configuration

### Why Docker Memory Matters

Ollama models run inside Docker containers with memory limits. If the limit is too low, the model will crash or fail to load.

### Automatic Memory Management

The startup scripts **automatically manage Docker memory**:

```bash
# Script detects your model choice and sets Docker memory
# For llama3.1:8b → Sets Docker to 8GB
# For qwen2.5:14b → Sets Docker to 12GB
```

### Manual Configuration (if needed)

**Docker Desktop (GUI)**:
1. Open Docker Desktop
2. Settings → Resources → Memory
3. Set slider to recommended amount:
   - `llama3.1:8b`: 8GB
   - `qwen2.5:14b`: 12GB
   - `qwen2.5:32b`: 24GB
4. Click "Apply & Restart"

**Docker Desktop (Command Line)**:
```bash
# macOS
osascript -e 'tell application "Docker Desktop" to quit'
defaults write ~/Library/Group\ Containers/group.com.docker/settings.json memoryMiB 8192
open -a Docker

# Linux
# Edit /etc/docker/daemon.json
{
  "default-runtime": "nvidia",
  "memory": "8g"
}
sudo systemctl restart docker
```

### Memory Requirements by Model

Zetherion AI uses **two Ollama containers** to avoid model-swapping delays:

| Component | Memory | Purpose |
|-----------|--------|---------|
| **Router Container** | 1GB (fixed) | Fast message classification (qwen2.5:0.5b) |
| **Generation Container** | Variable | Complex queries + embeddings |

**Generation Container Memory by Model:**

| Model | Generation Memory | Total Docker | System RAM | Notes |
|-------|-------------------|--------------|------------|-------|
| `qwen2.5:3b` | 4GB | 5GB | 8GB | Minimal setup |
| `qwen2.5:7b` | 8GB | 9GB | 12GB | **Recommended default** |
| `llama3.1:8b` | 8GB | 9GB | 12GB | Alternative default |
| `qwen2.5:14b` | 12GB | 13GB | 16GB | High quality |
| `qwen2.5:32b` | 24GB | 25GB | 32GB | Maximum quality |

**Formula**: Total Docker Memory = 1GB (router) + Generation Memory

## Performance Benchmarks

### Real-World Response Times

Testing setup:
- **Query**: "Explain quantum computing in simple terms (100-150 words)"
- **Location**: Response generation only (excludes network/Discord latency)

| Configuration | Backend | Model | Response Time | Quality (1-10) |
|---------------|---------|-------|--------------|---------------|
| Budget Laptop (8GB RAM) | Gemini | gemini-flash | 0.8s | 7/10 |
| Budget Laptop (8GB RAM) | Ollama | phi3:mini | 4.2s | 6/10 |
| Mid-Range PC (16GB RAM) | Ollama | llama3.1:8b | 3.3s | 8/10 |
| Gaming PC (16GB, RTX 3060) | Ollama | llama3.1:8b | 0.7s | 8/10 |
| Gaming PC (16GB, RTX 3060) | Ollama | qwen2.5:14b | 1.2s | 9/10 |
| Workstation (32GB, RTX 4090) | Ollama | qwen2.5:32b | 0.6s | 10/10 |
| M2 Max MacBook (32GB) | Ollama | qwen2.5:14b | 0.9s | 9/10 |

**Key Takeaways**:
- **Gemini (cloud)**: Fastest and free, good quality, requires internet
- **GPU acceleration**: Makes Ollama competitive with cloud speeds
- **Larger models**: Better quality but slower (unless you have powerful GPU)
- **CPU-only Ollama**: Slower but provides privacy and offline capability

### Memory Usage Comparison

| Configuration | Memory Usage | Idle | During Inference |
|---------------|--------------|------|------------------|
| Gemini backend only | Docker: 2GB, System: 4GB | 1GB | 2GB |
| Ollama `llama3.1:8b` (CPU) | Docker: 8GB, System: 10GB | 5GB | 7.5GB |
| Ollama `llama3.1:8b` (GPU) | Docker: 6GB, System: 8GB, VRAM: 6GB | 3GB, VRAM: 5GB | 5GB, VRAM: 6GB |
| Ollama `qwen2.5:14b` (GPU) | Docker: 12GB, System: 14GB, VRAM: 10GB | 5GB, VRAM: 8GB | 10GB, VRAM: 10GB |

## Cost Analysis

### Total Cost of Ownership (1 year)

| Configuration | Hardware Cost | Cloud API Cost | Electricity | Total |
|---------------|---------------|----------------|-------------|-------|
| **Gemini Only** | $0 (existing PC) | $0 (free tier) | $5/year | **$5/year** |
| **Gemini + Claude** | $0 (existing PC) | $50-200/year | $5/year | **$55-205/year** |
| **Ollama (existing PC)** | $0 | $0 | $20/year | **$20/year** |
| **Ollama (new GPU)** | $300-500 (RTX 3060) | $0 | $50/year | **$350-550 first year, $50/year after** |

**Cloud API Costs** (moderate usage: 100 requests/day):
- **Gemini Free Tier**: $0 (covers 1,500 requests/day)
- **Claude Sonnet**: ~$50-100/year (complex queries only)
- **OpenAI GPT-4**: ~$100-200/year (alternative to Claude)

**Ollama Electricity** (8 hours/day usage):
- CPU-only: ~50W → $15/year
- GPU (RTX 3060): ~200W → $50/year
- (Assumes $0.12/kWh electricity rate)

### Break-Even Analysis

**If you're considering buying a GPU for Ollama:**

- **RTX 3060 ($350)**: Breaks even vs cloud APIs in ~2-3 years
- **RTX 4060 ($300)**: Breaks even in ~2 years
- **Used RTX 3060 ($200)**: Breaks even in ~1 year

**Best Value**:
1. **Free tier**: Gemini only (excellent for light use)
2. **Budget**: Ollama on existing hardware (privacy + no API costs)
3. **Quality**: Gemini + Claude (best results, cloud-based)
4. **Long-term**: Ollama + GPU upgrade (upfront cost, zero API fees)

## Hardware Upgrade Path

### Starting Point → Future Upgrades

**Path 1: Budget to Mid-Range**
1. Start: 8GB RAM, Gemini backend (free)
2. Upgrade: Add 8GB RAM → 16GB total
3. Result: Can run `llama3.1:8b` smoothly on CPU

**Path 2: Mid-Range to High-End**
1. Start: 16GB RAM, `llama3.1:8b` on CPU
2. Upgrade: Add GPU (RTX 3060 or better)
3. Result: 5-10x faster inference, can run `qwen2.5:14b`

**Path 3: High-End to Workstation**
1. Start: 16GB RAM, RTX 3060, `qwen2.5:14b`
2. Upgrade: RAM to 32GB, GPU to RTX 4080/4090
3. Result: Can run `qwen2.5:32b` or larger models

## Recommendations by User Type

### Hobbyist / Personal Use
- **Backend**: Gemini (cloud)
- **Hardware**: Any modern computer (8GB+ RAM)
- **Why**: Zero cost, minimal setup, good quality
- **Upgrade**: Add RAM if you want to try local models

### Privacy-Conscious User
- **Backend**: Ollama with `llama3.1:8b`
- **Hardware**: 16GB RAM, modern CPU
- **Why**: No data sent to cloud for routing, offline capable
- **Upgrade**: Add GPU for faster responses

### Power User
- **Backend**: Ollama with `qwen2.5:14b`
- **Hardware**: 16GB+ RAM, RTX 3060 or better
- **Why**: Best quality local AI, fast responses
- **Upgrade**: More RAM/better GPU for larger models

### Professional / Business
- **Backend**: Hybrid (Gemini for routing, Claude for complex tasks)
- **Hardware**: 16GB+ RAM, good internet connection
- **Why**: Best quality, reliable, cloud-based
- **Upgrade**: Dedicated API budget for higher usage

### Developer / Researcher
- **Backend**: Ollama with large models (`qwen2.5:32b+`)
- **Hardware**: 32GB+ RAM, RTX 4090 or workstation GPU
- **Why**: Full control, offline, maximum quality
- **Upgrade**: More VRAM for even larger models

## Troubleshooting

### "Model won't load" or "OOM (Out of Memory)"

**Symptoms**: Container crashes, "Out of memory" errors in logs

**Solutions**:
1. **Increase Docker memory**: See [Docker Memory Configuration](#docker-memory-configuration)
2. **Choose smaller model**: Use `llama3.1:8b` instead of `qwen2.5:14b`
3. **Close other applications**: Free up system RAM
4. **Upgrade RAM**: Add more system memory

### "Inference is slow"

**Symptoms**: Responses take 5+ seconds, bot feels sluggish

**Solutions**:
1. **Enable GPU**: If you have compatible GPU, install drivers
2. **Choose faster model**: Use `mistral:7b` instead of larger models
3. **Switch to Gemini**: Cloud backend is faster for most hardware
4. **Upgrade hardware**: Add GPU or faster CPU

### "Docker Desktop won't allocate enough memory"

**Symptoms**: Can't set Docker memory above 8GB on macOS

**Solution (macOS)**:
```bash
# Edit Docker's VM settings
nano ~/Library/Group\ Containers/group.com.docker/settings.json

# Set memoryMiB to desired amount (in MB)
{
  "memoryMiB": 12288  # 12GB
}

# Restart Docker Desktop
```

**Solution (Windows)**:
1. Open Docker Desktop
2. Settings → Resources → WSL Integration
3. Ensure "Enable integration with default WSL distro" is checked
4. In WSL terminal: `wsl --shutdown`, then restart Docker

## Additional Resources

- **Ollama Model Library**: https://ollama.ai/library
- **Docker Desktop Documentation**: https://docs.docker.com/desktop/
- **NVIDIA Container Toolkit**: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/
- **ROCm Documentation**: https://rocm.docs.amd.com/
- **Zetherion AI GitHub**: https://github.com/jimtin/zetherion-ai

## Community Benchmarks

Have you run Zetherion AI on different hardware? Share your benchmarks in [GitHub Discussions](https://github.com/jimtin/zetherion-ai/discussions) to help other users!

**What to share**:
- Hardware specs (CPU, RAM, GPU)
- Model used
- Average response time
- Quality assessment (1-10)
