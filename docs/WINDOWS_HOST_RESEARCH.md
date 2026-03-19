# Windows Host Research

**Date**: 2026-03-19  
**Host**: `Computer-of-awesome.local` (`COMPUTER-OF-AWE`)  
**Method**: read-only SSH + PowerShell inspection from macOS  
**Scope**: runtime layout, CI/WSL layout, scheduled automation, disk posture, and cutover safety rules

## Executive Summary

The Windows host is split into two top-level working areas:

- `C:\ZetherionAI`
  - the live Zetherion runtime checkout
  - contains the active `.env`
  - owns the runtime, recovery, promotions, Discord canary, and disk-cleanup scripts
- `C:\ZetherionCI`
  - a separate CI/WSL/testing area
  - contains the owner-CI launcher runtime, a WSL import area, workspace copies, and agent source copies
  - is not itself a git repo root, but it contains repo checkouts under it

This means the host is not a single in-place deployment folder. It is a two-zone machine:

- runtime zone: `C:\ZetherionAI`
- CI/test/WSL zone: `C:\ZetherionCI`

That split needs to stay explicit in every future cutover and recovery plan.

## Host Baseline

- OS: Windows 10 Pro
- build: `26200`
- architecture: `64-bit`
- drive posture at inspection time:
  - `C:` used: `420.22 GB`
  - `C:` free: `55.2 GB`
  - `D:` present with minimal usage

Important top-level paths observed on `C:\`:

- `C:\ZetherionAI`
- `C:\ZetherionCI`
- `C:\actions-runner`
- `C:\deploy-runner.ps1`
- `C:\verify-windows-host.ps1`

## Zone 1: `C:\ZetherionAI` Runtime Deployment

### Role

`C:\ZetherionAI` is the live runtime checkout. It contains:

- the deployed runtime source tree
- the authoritative Windows-side `.env`
- the active runtime automation scripts under `scripts\windows`
- runtime state directories like `data`, `logs`, `.artifacts`, and `tmp`

### Important contents

Observed top-level entries include:

- `.env`
- `.artifacts`
- `.venv`
- `cgs`
- `data`
- `logs`
- `scripts`
- `src`
- `tests`
- `updater_sidecar`

### Git state at inspection time

- repo exists: yes
- HEAD: `de7dd227954e7ab9957aebe900abffcd24463414`
- branch: detached HEAD
- state: dirty, with a large number of tracked modifications and untracked files

Operational rule:

- do **not** force-reset or in-place switch `C:\ZetherionAI` during cutover without an explicit backup or alternate validation checkout/worktree plan

### Runtime scripts in use

Observed under `C:\ZetherionAI\scripts\windows`:

- `deploy-runner.ps1`
- `startup-recover.ps1`
- `runtime-watchdog.ps1`
- `verify-runtime.ps1`
- `register-resilience-tasks.ps1`
- `verify-resilience-tasks.ps1`
- `install-ci-worker.ps1`
- `disk-cleanup.ps1`
- `discord-canary-runner.ps1`
- `promotions-watch.ps1`

These are the real repo-local scripts the host automation points at for runtime operations.

## Zone 2: `C:\ZetherionCI` CI / WSL / Testing Area

### Role

`C:\ZetherionCI` is a separate operational area for:

- owner-CI worker launching
- WSL bootstrap and maintenance
- testing workspaces
- source copies used by CI/runtime support tooling

It is **not** a git repo root itself.

### Important contents

Observed top-level entries include:

- `agent-runtime`
- `agent-src`
- `artifacts`
- `networkservice-ubuntu`
- `workspaces`
- WSL/bootstrap helper scripts and logs:
  - `ns-enable-systemd.sh`
  - `ns-install-docker.sh`
  - `ns-verify-docker.sh`
  - `ns-import-ubuntu.log`
  - `ns-install-docker.log`
  - `ns-verify-docker.log`
- WSL import artifact:
  - `ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz`

### `agent-runtime`

`C:\ZetherionCI\agent-runtime` is the owner-CI launcher runtime. It contains:

- `run-ci-worker.ps1`
- `run-wsl-keepalive.ps1`
- `venv`
- lightweight probe scripts
- `home`

The launcher script explicitly sets:

- `ZETHERION_EXECUTION_BACKEND=wsl_docker`
- `ZETHERION_DOCKER_BACKEND=wsl_docker`
- `ZETHERION_WSL_DISTRIBUTION=Ubuntu`

It then launches:

- `C:\ZetherionCI\agent-runtime\venv\Scripts\python.exe -m zetherion_dev_agent.cli worker`

### `agent-src`

`C:\ZetherionCI\agent-src` is a git checkout used as a source copy for CI support tooling.

At inspection time:

- HEAD: `2dd88b77ba8aa5c7b111be09faa2f7810c744cec`
- branch: `main`
- state: dirty

It contains a full source tree plus Windows scripts that mirror runtime automation concerns.

### `workspaces`

`C:\ZetherionCI\workspaces` contains workspace copies used by testing/CI.

Observed children:

- `catalyst-group-solutions`
- `zetherion-ai`

At inspection time:

- `C:\ZetherionCI\workspaces\zetherion-ai`
  - is a git repo
  - HEAD: `f9c9bbff74f4b27aa083b65150204730d549f771`
  - branch: `codex/production-control-plane`
  - state: dirty
- `C:\ZetherionCI\workspaces\catalyst-group-solutions`
  - was present as a workspace directory
  - was not a git repo root at the inspected path

Operational rule:

- treat `C:\ZetherionCI` as a mutable CI/test environment, separate from the live runtime path
- do not assume the runtime and CI trees are on the same commit or branch

## Scheduled Automation Model

The scheduled task inventory makes the host split explicit.

### Runtime-oriented tasks

These point into `C:\ZetherionAI`:

- `ZetherionRuntimeWatchdog`
  - `C:\ZetherionAI\scripts\windows\runtime-watchdog.ps1`
- `ZetherionStartupRecover`
  - `C:\ZetherionAI\scripts\windows\startup-recover.ps1`
- `ZetherionDiskCleanup`
  - `C:\ZetherionAI\scripts\windows\disk-cleanup.ps1`
  - note: this task bridges into the CI root with `-CiRoot "C:\ZetherionCI"`
- `ZetherionDiscordCanary`
  - `C:\ZetherionAI\scripts\windows\discord-canary-runner.ps1`
- `ZetherionPostDeployPromotions`
  - `C:\ZetherionAI\scripts\windows\promotions-watch.ps1`

### CI / WSL-oriented tasks

These point into `C:\ZetherionCI`:

- `ZetherionOwnerCiWorker`
  - `C:\ZetherionCI\agent-runtime\run-ci-worker.ps1`
- `ZetherionWslKeepalive`
  - `C:\ZetherionCI\agent-runtime\run-wsl-keepalive.ps1`
- `ZetherionNsImportUbuntu`
  - imports Ubuntu into `C:\ZetherionCI\networkservice-ubuntu`
- `ZetherionNsInstallDocker`
  - runs `/mnt/c/ZetherionCI/ns-install-docker.sh`
- `ZetherionNsEnableSystemd`
  - runs `/mnt/c/ZetherionCI/ns-enable-systemd.sh`
- `ZetherionNsVerifyDocker`
  - runs `/mnt/c/ZetherionCI/ns-verify-docker.sh`

### Supporting platform tasks

- `ZetherionDockerAutoStart`
  - launches Docker Desktop

Operational conclusion:

- Windows automation already treats runtime deployment and CI/WSL support as distinct domains
- future cutover work should preserve that distinction rather than flatten it into one path

## WSL and Docker State

### WSL

Observed distros:

- `Ubuntu` - running - version 2
- `docker-desktop` - stopped - version 2

### Docker

Observed state at inspection time:

- Docker Desktop service: `Manual`, `Stopped`
- Docker context:
  - `default`
  - `desktop-linux` selected
- Linux engine was not serving the `dockerDesktopLinuxEngine` pipe

Operational conclusion:

- WSL itself is available
- the Linux Docker engine was **not ready** at inspection time
- runtime verification that depends on container operations must start by proving Docker Desktop is actually up and serving the Linux context

### Latest stabilization status

As of the latest live cutover work on `2026-03-19`, the host has been moved
past the initial discovery state:

- the dirty runtime tree at `C:\ZetherionAI` has been archived to:
  - `C:\ZetherionAI-precutover-20260318T183044Z`
- a clean cutover candidate now exists at:
  - `C:\ZetherionAI-cutover`
- the clean candidate is on:
  - `8cc65f39ce5710ff35ab24e406159009c0fe9e6b`
- the runtime `.env` was carried forward into the clean candidate path

Docker stabilization also progressed materially:

- Docker Desktop settings are now on policy in
  `C:\Users\james\AppData\Roaming\Docker\settings-store.json`
  - `AutoStart = true`
  - `MemoryMiB = 98304`
  - `SwapMiB = 0`
  - `AutoPauseTimedActivitySeconds = 0`
  - `UseResourceSaver = false`
- the original recovery path was writing `settings-store.json` with a UTF-8
  BOM from Windows PowerShell, which kept Docker Desktop from parsing the file
  reliably
- the repo-local Docker helper now rewrites that file as UTF-8 **without** BOM
- after the no-BOM rewrite, the Docker backend stopped crashing on
  `settings-store.json` parsing and began serving backend traffic again
- the interactive scheduled task `\ZetherionDockerAutoStart` had to be enabled
  and used as the preferred recovery path for Docker Desktop in the disconnected
  user session
- the current host now passes the repo-local Docker recovery and resilience
  checks:
  - Docker Desktop process and service healthy
  - `desktop-linux` reachable from `docker.exe`
  - `ZetherionDockerAutoStart` enabled and runnable
  - readiness receipt reports `docker_desktop_recoverable = true`

Current remaining cutover blocker:

- the clean cutover candidate at `C:\ZetherionAI-cutover` is **not running as a
  compose project yet**
- `docker compose ls` shows the live project still running from:
  - `C:\ZetherionAI\docker-compose.yml`
- `docker compose ps --format json` from `C:\ZetherionAI-cutover` returns no
  running services
- this is expected with the current compose topology because the repo pins
  container names such as `zetherion-ai-bot`, `zetherion-ai-postgres`, and
  `zetherion-ai-qdrant`, so the live tree and cutover tree cannot be started
  side by side without collision

Operational rule:

- future work should resume from this state and focus on **promotion-aware
  runtime validation**, not on redoing env harvest or Docker desktop repair
- pre-promotion checks can be run against the clean candidate path
- runtime verification that expects containers must run only after the candidate
  has been promoted into `C:\ZetherionAI` or after the live project has been
  intentionally stopped and replaced

### Effective Docker capacity note

Although Docker Desktop settings are now on policy, effective Docker memory is
still below the intended host target because WSL appears to be using its
default host-memory cap:

- Windows host physical RAM: about `128 GiB`
- effective Docker server memory: about `62.79 GiB`
- WSL `free -h` reported about `62 GiB`

This means:

- Docker Desktop configuration drift is fixed
- the remaining capacity improvement is a **WSL memory policy** issue, not a
  Docker Desktop settings issue
- changing that will likely require a controlled WSL/Docker restart and should
  be treated as a planned optimization step during cutover, not as a rediscovery task

## Disk and Capacity Findings

### Current posture

- `C:` free space: `55.2 GB`
- `C:` used space: `420.22 GB`

### Notable identified consumers

- `C:\Users\james\AppData\Local\Docker\wsl\disk\docker_data.vhdx`
  - about `61.01 GB`
- `C:\hiberfil.sys`
  - about `53.9 GB`
- `C:\ZetherionCI\networkservice-ubuntu`
  - about `17.75 GB`
- `C:\ZetherionCI\ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz`
  - about `0.36 GB`

The repo-local directories under `C:\ZetherionAI` were comparatively small at inspection time:

- `.venv`: about `0.33 GB`
- most other inspected runtime directories were near zero at the top-level scan

Operational conclusion:

- the biggest disk pressure drivers are not the checked-out runtime tree itself
- the major persistent consumers are:
  - Docker VHD
  - hibernation file
  - imported WSL storage area
  - accumulated CI/test workspace state

## Root-Level Scripts

Observed on `C:\`:

- `C:\deploy-runner.ps1`
- `C:\verify-windows-host.ps1`

These exist as standalone copies, but the scheduled task model primarily points into repo-local scripts under:

- `C:\ZetherionAI\scripts\windows`
- `C:\ZetherionCI\agent-runtime`

Operational rule:

- treat root-level scripts as host helpers or older convenience entrypoints
- for source-of-truth behavior, prefer the repo-local script paths and the task definitions that reference them

## Safe Cutover Rules

### What is already true

- the live environment split is understood:
  - `CGS` config comes from Vercel
  - `Zetherion` config comes from `C:\ZetherionAI\.env`
- the host already uses separate runtime and CI/WSL areas
- the current live environment should be treated as sufficient unless a new feature or a real runtime failure proves otherwise

### What must be true before any in-place cutover

1. Inspect `C:\ZetherionAI` git state again
2. Decide whether to:
   - validate using a clean alternate checkout/worktree, or
   - intentionally mutate the live runtime path after backup/snapshot
3. Prove Docker Desktop is actually serving the Linux engine
4. Verify the scheduled task model is still intact
5. Keep `TradeOxy` out of the first core cutover wave

### Implemented stabilization helpers

The repo now includes explicit Windows cutover helpers for this host model:

- `scripts\windows\prepare-runtime-cutover.ps1`
  - captures a forensic receipt from the current live tree
  - copies the dirty live runtime into a timestamped rescue path
  - creates a clean candidate checkout from the approved target SHA
  - carries forward only allowlisted host state such as `.env`
- `scripts\windows\promote-runtime-cutover.ps1`
  - stops the current runtime
  - moves the current live tree out of the way
  - promotes the clean candidate path into `C:\ZetherionAI`
  - emits a promotion receipt

The shared Docker recovery layer also now treats Docker Desktop as a first-class
contract:

- auto-start must be enabled
- memory must meet the host floor (`98304 MiB`)
- swap must be `0`
- auto-pause/resource-saver behavior must not block unattended recovery
- `desktop-linux` must be reachable
- WSL `docker.service` must be active

### What we should avoid

- forcing a branch switch or hard reset on `C:\ZetherionAI` without a rollback plan
- assuming `C:\ZetherionCI` can be ignored during runtime validation
- assuming the runtime checkout and the CI/test checkout are on the same branch or commit

## Recommended Next Steps

1. Preserve this document as the Windows host source of truth.
2. Resume from the prepared cutover state:
   - `C:\ZetherionAI-precutover-20260318T183044Z`
   - `C:\ZetherionAI-cutover`
3. Keep using the repaired Docker Desktop path:
   - `\ZetherionDockerAutoStart`
   - `desktop-linux` reachable from `docker.exe`
   - Docker settings enforced without BOM drift
4. Start Windows certification in two stages:
   - pre-promotion:
     - Docker Desktop Linux engine up
     - Docker Desktop resource settings match the host policy
     - owner-CI worker and WSL keepalive healthy
     - clean candidate path source-clean and ready
   - post-promotion:
     - runtime verification passes from `C:\ZetherionAI`
     - service health, bot markers, fallback probe, and database checks pass
5. Promote the clean candidate only when ready to replace the live compose
   project, because the current compose topology does not support side-by-side
   runtime startup from both paths.

## Summary Answer

The host does have the two separate top-level areas you expected, but they are directories, not files:

- `C:\ZetherionAI` = live runtime deployment
- `C:\ZetherionCI` = WSL/testing/owner-CI environment

That distinction is real, already embedded in the task automation, and should guide every future cutover.
