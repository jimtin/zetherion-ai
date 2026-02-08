# Claude Code Instructions — Zetherion AI

## Git Push Process

**Before every push, run `./scripts/pre-push-tests.sh` and confirm ALL steps pass. Only push after the script completes successfully.**

The push pipeline is:

1. **Ruff lint** — `ruff check src/ tests/`
2. **Unit tests** — `pytest tests/ -m "not integration and not discord_e2e"`
3. **In-process integration tests** — `pytest tests/integration/test_skills_http.py tests/integration/test_heartbeat_cycle.py tests/integration/test_profile_pipeline.py tests/integration/test_agent_skills_http.py tests/integration/test_skills_e2e.py tests/integration/test_user_isolation.py tests/integration/test_encryption_at_rest.py -m integration`
4. **Docker environment** — tear down any stale environment, build fresh images from current code (`docker compose -f docker-compose.test.yml up -d --build`), wait for ALL services healthy (postgres, qdrant, ollama, ollama-router, skills, bot)
5. **Ollama model pulls** — llama3.2:1b, nomic-embed-text, llama3.1:8b
6. **Docker E2E tests** — `DOCKER_MANAGED_EXTERNALLY=true pytest tests/integration/test_e2e.py -m integration`
7. **Discord E2E tests** — `DOCKER_MANAGED_EXTERNALLY=true pytest tests/integration/test_discord_e2e.py -m discord_e2e`
8. **Push** — only after all 7 steps pass, run `git push`

If any step fails, fix the code and re-run from step 1. Docker is torn down automatically on exit.

## Test Enforcement

- A direct git pre-push hook (`.git/hooks/pre-push`) runs `scripts/pre-push-tests.sh` automatically on every push.
- Run `./scripts/pre-push-tests.sh` before every push. Confirm it passes. Then push.
- Do not push without running the full pipeline first.
- Do not use `git push --no-verify` or any equivalent flag.
- If tests fail, fix the code — do not remove or weaken the tests.
- Docker Desktop must be running before pushing (the script checks for this).
- If the git hook is missing, reinstall it: `cp scripts/pre-push-tests.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push` (or create a wrapper that calls `exec ./scripts/pre-push-tests.sh`).

## Running Tests Manually

```bash
# Unit tests only
pytest tests/ -m "not integration and not discord_e2e" --tb=short -q

# In-process integration tests (no Docker needed)
pytest tests/integration/test_skills_http.py tests/integration/test_heartbeat_cycle.py tests/integration/test_profile_pipeline.py tests/integration/test_agent_skills_http.py tests/integration/test_skills_e2e.py tests/integration/test_user_isolation.py tests/integration/test_encryption_at_rest.py -m integration --tb=short -q

# Full pre-push suite (requires Docker)
./scripts/pre-push-tests.sh
```

## Project Structure

- **Source code**: `src/zetherion_ai/`
- **Unit tests**: `tests/`
- **Integration tests**: `tests/integration/`
- **Docker test config**: `docker-compose.test.yml`
- **Pre-push script**: `scripts/pre-push-tests.sh`
