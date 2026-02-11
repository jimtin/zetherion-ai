# Docker Services and Deployment

## Overview

Zetherion AI runs as 6 Docker services connected via an internal bridge network (`zetherion-ai-net`). The architecture uses distroless base images for application services, pinned image digests for third-party services, and resource limits on every container. Two separate Ollama containers eliminate model-swapping delays by keeping dedicated models loaded in memory at all times.

---

## Service Overview

| Service | Image | Port | Purpose | Memory (min-max) | CPU (min-max) |
|---------|-------|------|---------|-------------------|---------------|
| bot | Distroless (custom) | none | Discord gateway, agent logic | 512M - 2G | 0.5 - 2.0 |
| skills | Distroless (custom) | 8080 (internal) | Skills REST API | 128M - 512M | 0.25 - 1.0 |
| qdrant | qdrant/qdrant (pinned digest) | 6333 (host) | Vector database | 256M - 2G | 0.25 - 2.0 |
| postgres | postgres:17-alpine | 5432 (internal) | Relational database | 64M - 256M | 0.25 - 1.0 |
| ollama | ollama/ollama (pinned digest) | 11434 (host) | LLM generation + embeddings | 2G - 8G | 1.0 - 4.0 |
| ollama-router | ollama/ollama (pinned digest) | none | Query classification | 1.5G - 3G | 0.5 - 2.0 |

**Total resource envelope:** 4.5G - 16.25G memory, 2.75 - 12.0 CPU cores.

---

## Service Details

### Bot Service (zetherion-ai-bot)

The primary application container. Connects to Discord, runs the Agent Core, InferenceBroker, and security layer.

- **Build**: Custom `Dockerfile` using a Google distroless base image. No shell or package manager in the final image.
- **Filesystem**: Read-only root with `tmpfs` mounts at `/tmp` and `/home/nonroot/.cache` for writable temporary storage.
- **Security**: `no-new-privileges:true` prevents any privilege escalation.
- **Dependencies**: Waits for `qdrant` (healthy), `zetherion-ai-skills` (healthy), and `postgres` (healthy) before starting.
- **Volumes**:
  - `./data:/app/data` -- Persistent storage for encryption salt, SQLite cost database, and local state.
  - `./logs:/app/logs` -- Structured JSON log files with rotation (10MB x 6 files).
- **Environment Variables**:
  - `QDRANT_HOST=qdrant`, `QDRANT_PORT=6333`
  - `OLLAMA_HOST=ollama`, `OLLAMA_PORT=11434`
  - `OLLAMA_ROUTER_HOST=ollama-router`, `OLLAMA_ROUTER_PORT=11434`
  - `SKILLS_SERVICE_URL=http://zetherion-ai-skills:8080`
  - `POSTGRES_DSN=postgresql://zetherion:changeme@postgres:5432/zetherion`
  - `ENVIRONMENT=production`
  - Additional secrets loaded from `.env` file.
- **Resources**: 512M reserved, 2G limit. 0.5 CPU reserved, 2.0 CPU limit.
- **Ports**: None exposed to host. Communicates only over the internal network.

### Skills Service (zetherion-ai-skills)

An aiohttp REST API that provides the pluggable skills framework. Registers and manages TaskManager, Calendar, Profile, Gmail, and GitHub skills.

- **Build**: Custom `Dockerfile.skills` using a Google distroless base image.
- **Filesystem**: Read-only root with `tmpfs` at `/tmp` and `/home/nonroot/.cache`.
- **Security**: `no-new-privileges:true`.
- **Dependencies**: Waits for `qdrant` (healthy) and `postgres` (healthy) before starting.
- **Volumes**: None. All persistent data is stored in PostgreSQL and Qdrant.
- **Environment Variables**:
  - `QDRANT_HOST=qdrant`, `QDRANT_PORT=6333`
  - `OLLAMA_HOST=ollama`, `OLLAMA_PORT=11434`
  - `OLLAMA_ROUTER_HOST=ollama-router`, `OLLAMA_ROUTER_PORT=11434`
  - `POSTGRES_DSN=postgresql://zetherion:changeme@postgres:5432/zetherion`
  - `SKILLS_HOST=0.0.0.0`, `SKILLS_PORT=8080`
- **Resources**: 128M reserved, 512M limit. 0.25 CPU reserved, 1.0 CPU limit.
- **Ports**: 8080 (internal only). Not exposed to the host. Only the Bot Service communicates with this service via the internal Docker network.
- **Authentication**: Requires `X-API-Secret` header with HMAC token on all requests.

### Qdrant (zetherion-ai-qdrant)

Vector database for semantic memory storage. Provides cosine similarity search over encrypted embedding payloads.

- **Image**: `qdrant/qdrant:latest` pinned by SHA256 digest for reproducible builds. Dependabot automatically proposes digest updates.
- **Security**: `no-new-privileges:true`.
- **Ports**: 6333 exposed to host (provides both the REST API and the web dashboard).
- **Volume**: `qdrant_storage:/qdrant/storage` -- Persistent named volume for all vector data and indexes.
- **Health Check**: TCP connection test to port 6333 every 10 seconds, 5-second timeout, 5 retries, 10-second startup period.
- **Resources**: 256M reserved, 2G limit. 0.25 CPU reserved, 2.0 CPU limit.
- **TLS**: Certificate mount points are prepared in the compose file but commented out. Run `scripts/generate-qdrant-certs.sh` and uncomment the volume mounts to enable TLS.

### PostgreSQL (zetherion-ai-postgres)

Relational database for user management, RBAC, dynamic settings, personal understanding data, Gmail state, and GitHub audit logs.

- **Image**: `postgres:17-alpine` -- Lightweight Alpine-based PostgreSQL 17.
- **Security**: `no-new-privileges:true`.
- **Ports**: Internal only. No port exposed to the host. Only the Bot Service and Skills Service connect via the internal network.
- **Volume**: `postgres_data:/var/lib/postgresql/data` -- Persistent named volume for all relational data.
- **Health Check**: `pg_isready -U zetherion` every 10 seconds, 5-second timeout, 5 retries, 10-second startup period.
- **Resources**: 64M reserved, 256M limit. 0.25 CPU reserved, 1.0 CPU limit.
- **Default Credentials**: `POSTGRES_DB=zetherion`, `POSTGRES_USER=zetherion`, `POSTGRES_PASSWORD=password`. These must be changed for production deployments.

### Ollama Generation (zetherion-ai-ollama)

Local LLM container for privacy-sensitive inference and local embedding generation.

- **Image**: `ollama/ollama:latest` pinned by SHA256 digest.
- **Security**: `no-new-privileges:true`.
- **Ports**: 11434 exposed to host for debugging, direct model interaction, and health monitoring.
- **Volume**: `ollama_models:/root/.ollama` -- Persistent named volume for downloaded model weights. Models are downloaded once and cached across container restarts.
- **Health Check**: TCP connection test to port 11434 every 30 seconds, 10-second timeout, 3 retries, 60-second startup period. The longer startup period accounts for initial model loading.
- **Resources**: 2G reserved, 8G limit. 1.0 CPU reserved, 4.0 CPU limit.
- **Models**:
  - `llama3.1:8b` -- Primary generation model (8 billion parameters). Handles complex queries when privacy is required.
  - `nomic-embed-text` -- Local embedding model (768 dimensions). Ensures sensitive text never leaves the local network.

### Ollama Router (zetherion-ai-ollama-router)

Dedicated lightweight Ollama container for fast query classification. Keeps a small model loaded in memory at all times for sub-second routing decisions.

- **Image**: `ollama/ollama:latest` pinned by SHA256 digest (same image as generation container).
- **Security**: `no-new-privileges:true`.
- **Ports**: None exposed to host. Internal network access only.
- **Volume**: `ollama_router_models:/root/.ollama` -- Separate persistent volume from the generation container. Each container manages its own model cache independently.
- **Health Check**: TCP connection test to port 11434 every 30 seconds, 10-second timeout, 3 retries, 30-second startup period. Faster startup than the generation container due to the smaller model.
- **Resources**: 1.5G reserved, 3G limit. 0.5 CPU reserved, 2.0 CPU limit.
- **Model**: `llama3.2:3b` -- Small classification model (3 billion parameters). Fast enough for real-time query routing on CPU.

---

## Network Architecture

All 6 services are connected to a single Docker bridge network named `zetherion-ai-net`.

```
                    Host Machine
                         |
          +--------------+--------------+
          |              |              |
     port 6333      port 11434     (no other
     (Qdrant)       (Ollama)      host ports)
          |              |
+---------+--------------+--------------+
|            zetherion-ai-net            |
|                                       |
|  +-----+  +------+  +------+         |
|  | bot |--| skills|--| qdrant|        |
|  +--+--+  +--+---+  +-------+        |
|     |        |                        |
|  +--+--------+---+  +-------------+  |
|  |   postgres    |  | ollama      |  |
|  +---------------+  +-------------+  |
|                      +-------------+  |
|                      | ollama-     |  |
|                      | router      |  |
|                      +-------------+  |
+---------------------------------------+
```

**Service Discovery**: Containers reference each other by container name (e.g., `http://qdrant:6333`, `http://zetherion-ai-skills:8080`). Docker's built-in DNS resolves these names to container IP addresses on the bridge network.

**Host Exposure**: Only two services expose ports to the host:
- Qdrant on port 6333 (dashboard and API access for debugging)
- Ollama generation on port 11434 (direct model interaction for debugging)

All other services (bot, skills, postgres, ollama-router) are accessible only from within the Docker network.

---

## Volumes

| Volume | Type | Service | Purpose |
|--------|------|---------|---------|
| `qdrant_storage` | Named | qdrant | Vector embeddings, indexes, and collection metadata |
| `ollama_models` | Named | ollama | Generation model weights (llama3.1:8b, nomic-embed-text) |
| `ollama_router_models` | Named | ollama-router | Router model weights (llama3.2:3b) |
| `postgres_data` | Named | postgres | Relational data (users, settings, profiles, Gmail, GitHub) |
| `./data` | Bind mount | bot | Encryption salt file, SQLite cost database, local state |
| `./logs` | Bind mount | bot | Structured JSON application logs with rotation |

Named volumes are managed by Docker and persist across container restarts, removals, and image updates. Bind mounts (`./data`, `./logs`) map host directories directly into the container for easy access to logs and data files.

---

## Security Hardening

Every container in the stack is hardened with multiple security controls:

**Distroless Base Images (bot, skills)**
- No shell (`/bin/sh`, `/bin/bash`) present in the image
- No package manager (`apt`, `apk`) available
- Minimal filesystem containing only the Python runtime and application code
- Significantly reduced attack surface compared to standard base images

**Read-Only Root Filesystem**
- The root filesystem is mounted read-only via `read_only: true`
- Writable directories are provided through `tmpfs` mounts at `/tmp` and `/home/nonroot/.cache`
- Prevents attackers from writing persistent malware or modifying application code

**No-New-Privileges**
- The `no-new-privileges:true` security option is set on all 6 containers
- Prevents any process from gaining additional privileges via `setuid`, `setgid`, or capability escalation

**Resource Limits**
- Every container has both CPU and memory limits defined under the `deploy.resources` section
- Prevents any single container from consuming all host resources
- Protects against denial-of-service through resource exhaustion

**Network Isolation**
- All services communicate over an isolated Docker bridge network
- Only 2 of 6 services expose ports to the host
- PostgreSQL, Skills Service, and Ollama Router are completely internal

---

## Health Checks

All services define health checks that Docker uses to determine readiness. The Bot Service depends on three other services being healthy before it starts, and the Skills Service depends on two.

| Service | Check Method | Interval | Timeout | Retries | Start Period |
|---------|-------------|----------|---------|---------|--------------|
| bot | Dockerfile HEALTHCHECK | -- | -- | -- | -- |
| skills | Dockerfile HEALTHCHECK | -- | -- | -- | -- |
| qdrant | TCP port 6333 | 10s | 5s | 5 | 10s |
| postgres | `pg_isready -U zetherion` | 10s | 5s | 5 | 10s |
| ollama | TCP port 11434 | 30s | 10s | 3 | 60s |
| ollama-router | TCP port 11434 | 30s | 10s | 3 | 30s |

**Startup Order** (enforced via `depends_on` with `condition: service_healthy`):

1. `qdrant` and `postgres` start first (no dependencies)
2. `zetherion-ai-skills` starts after `qdrant` and `postgres` are healthy
3. `zetherion-ai-bot` starts after `qdrant`, `postgres`, and `zetherion-ai-skills` are healthy
4. `ollama` and `ollama-router` start independently (no `depends_on` constraints from other services, but the bot connects to them at runtime)

---

## Dual Ollama Architecture

The system runs two separate Ollama containers rather than a single shared instance. This is a deliberate architectural decision driven by Ollama's model management behavior.

**The Problem with a Single Container**

Ollama loads one model into memory at a time. When a request arrives for a different model, Ollama must:
1. Unload the current model from memory (1-3 seconds)
2. Load the requested model from disk (2-10 seconds depending on model size)
3. Process the request

In a single-container setup, every routing request would trigger a model swap from the generation model to the router model, and every generation request would trigger a swap back. This adds 4-20 seconds of latency to every interaction.

**The Two-Container Solution**

- **ollama-router** keeps `llama3.2:3b` (3B parameters) loaded at all times. This model is small enough to classify queries in under 500ms on CPU, and the container only needs 1.5G-3G of memory.
- **ollama** keeps `llama3.1:8b` (8B parameters) and `nomic-embed-text` loaded for generation and embedding tasks. This container has a larger resource allocation (2G-8G memory) to accommodate the bigger model.

Each container maintains its own model cache via separate named volumes (`ollama_models` and `ollama_router_models`), ensuring that model downloads are independent and persistent.

---

## GPU Support

Optional NVIDIA GPU acceleration can be enabled for the Ollama generation container. This significantly improves inference speed for the 8B parameter model.

To enable GPU support, modify the `ollama` service in `docker-compose.yml`:

```yaml
ollama:
  image: ollama/ollama:latest@sha256:...
  container_name: zetherion-ai-ollama
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

**Requirements:**
- NVIDIA GPU with CUDA support
- NVIDIA Container Toolkit (`nvidia-docker2`) installed on the host
- Docker runtime configured to use the NVIDIA runtime

GPU acceleration is not required. The system runs entirely on CPU with acceptable performance for personal use.

---

## Managing Services

### Starting and Stopping

```bash
# Start all services in detached mode
docker-compose up -d

# Stop all services and remove containers
docker-compose down

# Stop all services and remove containers AND volumes (destroys data)
docker-compose down -v

# Restart a single service
docker-compose restart zetherion-ai-bot

# Rebuild and restart after code changes
docker-compose up -d --build
```

### Monitoring

```bash
# View logs for a specific service (follow mode)
docker-compose logs -f zetherion-ai-bot

# View logs for all services
docker-compose logs -f

# Check resource usage for all containers
docker stats

# Check health status of all services
docker-compose ps
```

### Model Management

```bash
# Pull the generation model into the ollama container
docker exec zetherion-ai-ollama ollama pull llama3.1:8b

# Pull the embedding model
docker exec zetherion-ai-ollama ollama pull nomic-embed-text

# Pull the router model into the router container
docker exec zetherion-ai-ollama-router ollama pull llama3.2:3b

# List models in each container
docker exec zetherion-ai-ollama ollama list
docker exec zetherion-ai-ollama-router ollama list
```

---

## Related Documentation

- [System Architecture](architecture.md) -- High-level architecture, request flow, and component design
- [Security Architecture](security.md) -- Comprehensive security controls and threat model
- [Configuration Guide](configuration.md) -- Environment variables, settings hierarchy, and secrets management
