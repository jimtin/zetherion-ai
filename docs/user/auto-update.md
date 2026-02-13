# Auto-Update Runbook

## How Auto-Update Works

Auto-update watches GitHub Releases for `AUTO_UPDATE_REPO`.

When a new tag is available:

1. Updater sidecar fetches tags and checks out the release tag
2. Builds the inactive blue/green containers from GitHub source
3. Health-checks inactive services
4. Switches Traefik routing to the new color
5. Restarts bot process (graceful reconnect)
6. Stops old color services

## Rollback and Pause Behavior

If rollout fails at any stage:

- Sidecar attempts immediate rollback to previous git SHA
- Traffic is switched back to last healthy color
- Updates are paused (`423 Locked` on apply)

Paused state is stored in `/app/data/updater-state.json`.

## How to Unpause Updates

Call updater sidecar:

```bash
curl -X POST http://zetherion-ai-updater:9090/update/unpause \
  -H "X-Updater-Secret: <shared-secret>"
```

Or use the skill intent:

- `resume updates`
- `unpause updates`

## Known Limitation

`skills` and `api` are hot-swapped with blue/green routing. The Discord bot remains single-active and uses graceful reconnect on rollout.

## Troubleshooting

### Auth mismatch (401/403)

- Confirm `UPDATER_SECRET` matches sidecar secret, or leave it empty and use shared `UPDATER_SECRET_PATH`.
- Verify both skills and updater containers mount `./data:/app/data`.

### Rollouts paused (423)

- Check sidecar status (`/status`) for `pause_reason`.
- Fix underlying issue, then unpause with `/update/unpause`.

### GitHub rate limit / check failures

- Set `GITHUB_TOKEN` to reduce release API rate limits.
- Confirm `AUTO_UPDATE_REPO` is valid (`owner/repo`).

### Health check failures

- Verify Traefik dynamic route file exists: `config/traefik/dynamic/updater-routes.yml`.
- Check service health endpoints for both colors.
