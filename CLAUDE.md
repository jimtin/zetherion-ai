# Claude Code Instructions — Zetherion AI

## Git Push Policy

**NEVER use `--no-verify` when pushing code.** The pre-push hook runs the full test suite and must not be bypassed under any circumstances.

Before every `git push`, the pre-push hook (`scripts/pre-push-tests.sh`) automatically runs:

1. **Ruff lint** — `ruff check src/ tests/`
2. **Unit tests** — `pytest tests/ -m "not integration and not discord_e2e"`
3. **In-process integration tests** — `pytest tests/integration/test_skills_http.py tests/integration/test_heartbeat_cycle.py tests/integration/test_profile_pipeline.py tests/integration/test_agent_skills_http.py tests/integration/test_skills_e2e.py tests/integration/test_user_isolation.py tests/integration/test_encryption_at_rest.py -m integration`
4. **Docker environment startup** — `docker compose -f docker-compose.test.yml up -d` (waits for healthy services)
5. **Ollama model pulls** — llama3.2:1b, nomic-embed-text, llama3.1:8b
6. **Docker E2E tests** — `pytest tests/integration/test_e2e.py -m integration`
7. **Discord E2E tests** — `pytest tests/integration/test_discord_e2e.py -m discord_e2e`

If any step fails, the push is blocked. Docker is torn down automatically on exit.

## Test Enforcement Rules

- All tests must pass before code is pushed. No exceptions.
- Do not skip, disable, or work around the pre-push hook.
- Do not use `git push --no-verify` or any equivalent flag.
- If tests fail, fix the code — do not remove or weaken the tests.
- Docker Desktop must be running before pushing (the hook checks for this).

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
