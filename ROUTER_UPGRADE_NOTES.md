# Router Model Upgrade: llama3.2:1b → llama3.2:3b

## Summary

Based on a comprehensive benchmark (4,200 API calls, 7 models, 4 prompt variations, 3 runs each), the router model is being upgraded from `llama3.2:1b` (71.7% accuracy) to `llama3.2:3b` (96.0% accuracy).

## Benchmark Results

Full results at: `benchmarks/results/benchmark_20260211_080714.md`

| Model | Weighted Acc | Latency | Memory |
|-------|-------------|---------|--------|
| llama3.2:3b (new) | 96.0% | 800ms | ~2.0 GB |
| phi3:mini (runner-up) | 96.3% | 885ms | ~2.3 GB |
| llama3.2:1b (old) | 71.7% | 732ms | ~1.3 GB |
| Cloud models (baseline) | 100% | varies | N/A |

The "Are you there?" misclassification bug is fixed — llama3.2:3b correctly classifies it as `simple_query`.

## Files Changed (Router Upgrade)

### Production Code
- `src/zetherion_ai/config.py` — Default model: `llama3.2:1b` → `llama3.2:3b`
- `.env.example` — Default and recommendation updated
- `docker-compose.yml` — Comment updated (memory was already 3G from earlier fix)
- `docker-compose.test.yml` — Memory: 1G → 3G, reservation: 512M → 1536M
- `scripts/pre-push-tests.sh` — Model pull: `llama3.2:1b` → `llama3.2:3b`
- `CLAUDE.md` — Model pull reference updated

### Test Files
- `tests/test_config.py` — 3 assertions + 2 env vars updated
- `tests/integration/test_e2e.py` — Default model param + hardcoded reference updated
- `tests/test_self_healer.py` — 3 mock model name references updated
- `tests/test_router_ollama.py` — 2 references updated
- `tests/unit/test_router_ollama.py` — 3 references updated
- `tests/unit/test_router_factory.py` — 1 mock setting updated
- `tests/unit/test_interactive_setup.py` — 2 references updated

### Documentation (11 files)
All `docs/` files with `llama3.2:1b` references updated to `llama3.2:3b`, with related size/memory figures adjusted.

### New Files (Benchmark Infrastructure)
- `scripts/benchmark-router.py` — Benchmark script (~830 lines)
- `benchmarks/results/benchmark_20260211_080714.md` — Markdown report
- `benchmarks/results/benchmark_20260211_080714.json` — Full JSON results (gitignored)

## Verification Status

- Ruff lint: PASSED
- Unit tests: NOT YET RUN (venv `anyio` module issue — needs `pip install --force-reinstall anyio`)
- Integration tests: NOT YET RUN
- No remaining `llama3.2:1b` references outside benchmark files (verified with grep)

## Notes

- The V0 prompt (current production prompt) is the best performing — no prompt changes needed
- Docker memory for router was already bumped to 3G in a prior session (the memory starvation fix)
- The benchmark script is a standalone tool, not imported by production code
