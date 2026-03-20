# Windows Docker Maintenance Runbook

## Purpose

This runbook separates two different maintenance jobs on the Windows production
host:

- live-safe cleanup
  - remove stale Docker objects while the runtime stays online
- offline VHD compaction
  - shrink Docker Desktop's backing VHDX after cleanup has already freed space

These are not the same operation:

- `docker system prune` and related cleanup free space inside Docker
- VHD compaction gives that freed space back to `C:`

Current host assumptions:

- runtime checkout: `C:\ZetherionAI`
- CI / artifact root: `C:\ZetherionCI`
- Docker VHD path:
  `C:\Users\james\AppData\Local\Docker\wsl\disk\docker_data.vhdx`
- supported WSL distro: `Ubuntu`

## What To Preserve

The live runtime tree contains host state that must survive promotions and
maintenance:

- `C:\ZetherionAI\.env`
- `C:\ZetherionAI\data\certs`
- `C:\ZetherionAI\data\secrets`
- `C:\ZetherionAI\data\discord-canary`
- `C:\ZetherionAI\data\announcements`
- `C:\ZetherionAI\data\promotions`
- `C:\ZetherionAI\data\replay_chunks`
- `C:\ZetherionAI\data\profiles.db`
- `C:\ZetherionAI\data\salt.bin`

Do not treat these as disposable cleanup targets.

## Normal Cleanup

### Goal

Keep Docker logically small without taking the runtime offline.

### When To Run

Run the standard cleanup path:

- on the existing `ZetherionDiskCleanup` schedule
- after large certification / E2E waves
- after repeated one-off runner activity

### Expected Behavior

The standard cleanup path is [`disk-cleanup.ps1`](/Users/jameshinton/Developer/zetherion-ai/scripts/windows/disk-cleanup.ps1),
which relies on [`docker-runtime.ps1`](/Users/jameshinton/Developer/zetherion-ai/scripts/windows/docker-runtime.ps1).

It should:

- prune stopped containers
- prune unused networks
- prune dangling and stale test images
- prune builder cache on the tighter retention window
- prune unattached disposable volumes
- remove forbidden production volumes if they reappear:
  - `zetherionai_ollama_models`
  - `zetherionai_ollama_router_models`

It must not remove:

- active Postgres volume data
- active Qdrant volume data
- current live runtime images
- current candidate images during an active validation window

### Command

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ZetherionAI\scripts\windows\disk-cleanup.ps1 -CiRoot C:\ZetherionCI -Aggressive
```

### Receipt

Read:

- `C:\ZetherionCI\artifacts\disk-cleanup-receipt.json`

Healthy receipt expectations:

- `status = "cleaned"`
- `warnings = []`
- `build_cache_retention_hours = 6`
- `stale_test_image_retention_hours = 2`
- forbidden production volumes listed in the receipt contract

### Post-Cleanup Check

```powershell
docker system df
```

Healthy shape:

- build cache near `0B`
- only the expected live volumes present
- no `ollama` production volumes

## When To Compact The Docker VHD

### Use Compaction Only When

Use offline compaction when one of these is true:

- `docker_data.vhdx` is still much larger than logical Docker usage after cleanup
- `C:` free space is drifting down even though normal cleanup is succeeding
- a large certification / build wave has just completed and freed a lot of Docker
  content

### Do Not Compact During

- active production traffic if a short interruption is unacceptable
- a deployment that is still mid-promotion
- a state where Docker Desktop or WSL is still actively using the VHD

## Offline VHD Compaction Procedure

### 1. Capture Baseline

```powershell
git -C C:\ZetherionAI rev-parse --short HEAD
docker system df
Get-Item C:\Users\james\AppData\Local\Docker\wsl\disk\docker_data.vhdx | Select Length
Get-PSDrive C
```

Save the output into `C:\ZetherionCI\artifacts` if this is a planned maintenance
window.

### 2. Stop The Runtime Cleanly

Preferred path:

```powershell
cd C:\ZetherionAI
docker compose down --remove-orphans
```

If this follows a clean candidate promotion, do not restart the stack before the
compaction step.

### 3. Fully Release Docker Desktop And WSL

```powershell
Get-Process | Where-Object { $_.ProcessName -match 'docker|com\.docker' } | Stop-Process -Force -ErrorAction SilentlyContinue
Stop-Service -Name com.docker.service -Force -ErrorAction SilentlyContinue
wsl --shutdown
```

Confirm Docker is really down:

```powershell
Get-Service com.docker.service
Get-Process | Where-Object { $_.ProcessName -match 'docker|com\.docker' }
wsl --list --running
```

Expected result:

- Docker Desktop service stopped or not running
- no `Docker Desktop` / `com.docker.backend` process
- no running WSL distributions

### 4. Compact The VHD

Create a `diskpart` script with exactly:

```text
select vdisk file="C:\Users\james\AppData\Local\Docker\wsl\disk\docker_data.vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
```

Then run:

```powershell
diskpart /s <path-to-script>
```

### 5. Restart Through The Supported Recovery Path

Preferred restart:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ZetherionAI\scripts\windows\startup-recover.ps1 -DeployPath C:\ZetherionAI -WslDistribution Ubuntu
```

If `startup-recover.ps1` reports a Docker Desktop pipe error immediately after a
compaction, first confirm Docker Desktop actually relaunched before treating it
as a runtime failure.

### 6. Verify

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ZetherionAI\scripts\windows\verify-runtime.ps1 -DeployPath C:\ZetherionAI -OutputPath C:\ZetherionAI\data\verify-runtime-receipt.json
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ZetherionAI\scripts\windows\disk-cleanup.ps1 -CiRoot C:\ZetherionCI -Aggressive
docker system df
Get-Item C:\Users\james\AppData\Local\Docker\wsl\disk\docker_data.vhdx | Select Length
Get-PSDrive C
```

Success means:

- runtime verification is green
- cleanup receipt is green
- `docker_data.vhdx` is smaller than baseline
- `C:` free space is higher than baseline

## Troubleshooting

### Symptom: Docker Desktop pipe missing

Example:

- `//./pipe/dockerDesktopLinuxEngine` not found

Meaning:

- Docker Desktop itself did not come back yet
- or PowerShell surfaced a Docker probe error too early

First checks:

```powershell
Get-Service com.docker.service
Get-Process | Where-Object { $_.ProcessName -match 'docker|com\.docker' }
```

### Symptom: Ports already in use after a failed restart

Common ports:

- `127.0.0.1:18080`
- `127.0.0.1:18443`

Meaning:

- stale `docker-proxy` processes inside WSL still hold port forwards from a
  partially created compose stack

First checks:

```powershell
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18080 -State Listen
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18443 -State Listen
wsl -d Ubuntu -u root -- bash -lc "ss -ltnp '( sport = :18080 or sport = :18443 )' || true"
```

Recovery:

- bring the compose stack down again
- clear the stale `docker-proxy` holders
- restart compose cleanly

### Symptom: Postgres or Qdrant cannot start TLS

Examples:

- missing `server.crt`
- missing `/qdrant/certs/server.crt`

Meaning:

- host runtime state under `C:\ZetherionAI\data` was not carried forward into
  the live tree

Required directories:

- `C:\ZetherionAI\data\certs`
- `C:\ZetherionAI\data\secrets`

### Symptom: Cleanup script fails on missing Docker volume

Meaning:

- Docker stderr was surfaced as a PowerShell exception instead of a normal
  command result

Current fix:

- [`docker-runtime.ps1`](/Users/jameshinton/Developer/zetherion-ai/scripts/windows/docker-runtime.ps1)
  now captures native stderr as output for Docker / WSL result wrappers so the
  cleanup path can inspect exit codes safely

## Recommended Cadence

- Normal cleanup: keep the existing scheduled task
- Post-certification cleanup: run once after large test / certification waves
- VHD compaction: only after large cleanup events or when `C:` free space trend
  justifies a short maintenance window

## Current Healthy Reference Point

After the 2026-03-20 maintenance window:

- host runtime commit: `aecbf0e`
- Docker VHD reduced from about `61.86 GB` to about `10.53 GB`
- `C:` free space increased to about `236 GB`
- runtime verification green
- cleanup receipt green
