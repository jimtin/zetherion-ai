# Docker Services and Deployment

## Overview

Zetherion AI runs as a multi-container Docker stack on `zetherion-ai-net` with blue/green deployments for `skills` and `api`.

Production topology:

- `zetherion-ai-bot` (single active Discord gateway process)
- `zetherion-ai-skills-blue` / `zetherion-ai-skills-green`
- `zetherion-ai-api-blue` / `zetherion-ai-api-green`
- `zetherion-ai-traefik` (internal traffic switch)
- `zetherion-ai-updater` (GitHub tag pull + build + rollout orchestration)
- `zetherion-ai-cloudflared` (public API tunnel)
- `postgres`, `qdrant`, `ollama`, `ollama-router`

## Traffic Model

- Bot -> Skills: `http://zetherion-ai-traefik:8080`
- Routed Skills backend: blue or green (`config/traefik/dynamic/updater-routes.yml`)
- Routed API backend: blue or green (same route file, API entrypoint `:8443`)
- Cloudflared depends on Traefik and API availability; tunnel routing is managed in Cloudflare.

## Blue/Green Update Flow

Updater sidecar (`zetherion-ai-updater`) performs updates from GitHub release tags using local source builds:

1. `git fetch --tags` and checkout target release tag
2. Build inactive color (`skills` + `api`) and bot images
3. Start inactive color and verify direct health
4. Flip Traefik route file to inactive color
5. Verify routed health through Traefik
6. Restart bot (graceful reconnect path)
7. Stop old color services

On failure:

- Immediate rollback to previous git SHA
- Route flips back to prior healthy color
- Rollouts are paused until `/update/unpause` is called

Runtime updater state is persisted in `/app/data/updater-state.json`.

## Key Compose Controls

- `AUTO_UPDATE_PAUSE_ON_FAILURE`: pause future rollouts after first failed attempt
- `UPDATER_SECRET_PATH`: shared secret file for skills <-> updater auth
- `UPDATER_STATE_PATH`: persisted active color / pause state
- `UPDATER_TRAEFIK_DYNAMIC_PATH`: route file written by sidecar

## Service Summary

| Service | Role | External Port |
|---|---|---|
| `zetherion-ai-bot` | Discord bot runtime | none |
| `zetherion-ai-skills-blue/green` | Skills API | none |
| `zetherion-ai-api-blue/green` | Public API app | none |
| `zetherion-ai-traefik` | Internal router / blue-green switch | none |
| `zetherion-ai-updater` | Update orchestrator | none |
| `zetherion-ai-cloudflared` | Cloudflare tunnel | none |
| `postgres` | Relational storage | none |
| `qdrant` | Vector memory | `6333` |
| `ollama` | Generation + embeddings | `11434` |
| `ollama-router` | Routing model | none |

## Notes

- Bot remains single-instance by design to avoid dual active Discord gateway sessions.
- Zero-downtime guarantee applies to `skills`/`api` route switching; bot uses graceful reconnect.
- Dynamic route defaults are committed at `config/traefik/dynamic/updater-routes.yml`.
