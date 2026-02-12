# Plan: `client_app_tester` Skill — Automated UI Testing

## Context

James develops and deploys web applications for clients. Currently, verifying that these apps work correctly after deployment requires manual checking — opening the site, clicking through flows, checking different screen sizes. This is tedious, easy to forget, and doesn't scale.

This skill automates that process: it simulates real user behavior (mouse moves, clicks, form fills) on published web apps using Playwright, takes screenshots for visual verification, and runs on a configurable schedule (periodic + on-deploy via dev_watcher integration).

**Goal:** Catch broken buttons, overflowing layouts, failed forms, and missing functionality — automatically, without paid LLM calls.

---

## Feasibility Assessment

### What IS feasible (and how we'll do it)

| Requirement | Feasible? | Approach |
|---|---|---|
| Simulate real user behavior (mouse, clicks, forms) | Yes | Playwright — industry-standard browser automation, full async Python API |
| Periodic testing (e.g. every 24h) | Yes | Heartbeat integration — beat counter triggers runs at configured intervals |
| On-deploy testing (triggered by commits) | Yes | Dev watcher integration — detect deploy events, trigger test run via skill request |
| Screenshots during tests | Yes | Playwright's `page.screenshot()` — native, fast, configurable |
| Visual overflow/layout detection | Yes | Pixel-diff comparison against baseline screenshots using Pillow — zero LLM cost |
| Multiple screen sizes | Yes | Playwright viewport configuration — desktop, tablet, mobile, or custom |
| Screenshot storage and viewing | Yes | Filesystem storage + PostgreSQL metadata + API endpoint for serving images |
| Auto-cleanup | Yes | Retention policy — delete screenshots/results older than N days on each heartbeat |
| Minimize LLM costs | Yes | Zero LLM usage — YAML flows are deterministic, pixel-diff is mathematical |

### What requires your input (honest assessment)

**Auto-generating test flows from source code is NOT feasible.** The skill cannot look at your React/Next.js code and automatically figure out what buttons exist, what forms to fill, or what the correct behavior should be. This would require:
- Parsing arbitrary frontend frameworks (React, Vue, Svelte, vanilla JS)
- Understanding application state machines
- Knowing what "correct" looks like

**Instead:** You define test flows in YAML. This is actually better because:
- You know what the app should do — the YAML captures that knowledge
- Flows are deterministic and reproducible
- Easy to version control alongside the app
- No LLM cost per run (a Playwright flow costs ~$0.00)

---

## Architecture

```
                    zetherion-ai-skills (8080)
                         |
         +---------------+----------------+
         |                                |
  client_app_tester skill          dev_watcher skill
    (orchestration only)            (deploy detection)
         |                                |
         |  HTTP request to runner        |  HeartbeatAction
         v                                v
  zetherion-test-runner (9090)     triggers test run
    [Playwright + Chromium]
    [Separate Docker container]
         |
         v
    Target web apps (internet)
```

**Why a separate container?** The skills service uses a Chainguard distroless image — no shell, no package manager, definitely no Chromium. Playwright needs a full Linux userspace with browser binaries (~400MB). Keeping it separate also means:
- Skills service stays lean and secure
- Test runner can be scaled/restarted independently
- Resource limits can be set separately (browser automation is memory-hungry)

---

## Input: YAML Flow Definitions

Each app gets a YAML file defining its test flows. Stored in `data/test_flows/`.

### Example: `bobs_plumbing.yaml`

```yaml
name: "Bob's Plumbing Website"
base_url: "https://bobsplumbing.com"
viewports:
  - { width: 1920, height: 1080, name: "desktop" }
  - { width: 768, height: 1024, name: "tablet" }
  - { width: 375, height: 812, name: "mobile" }

auth:                              # Optional — see Auth section below
  type: "form"
  steps:
    - { action: "goto", url: "/admin/login" }
    - { action: "fill", selector: "#email", value: "${BOBS_ADMIN_EMAIL}" }
    - { action: "fill", selector: "#password", value: "${BOBS_ADMIN_PASSWORD}" }
    - { action: "click", selector: "button[type=submit]" }
    - { action: "wait_for", selector: ".dashboard", timeout_ms: 5000 }

flows:
  - name: "Homepage loads"
    steps:
      - { action: "goto", url: "/" }
      - { action: "wait_for", selector: "nav" }
      - { action: "screenshot", name: "homepage" }
      - { action: "assert_visible", selector: "a[href='/contact']" }
      - { action: "assert_text", selector: "h1", contains: "Bob's Plumbing" }

  - name: "Contact form submission"
    steps:
      - { action: "goto", url: "/contact" }
      - { action: "fill", selector: "#name", value: "Test User" }
      - { action: "fill", selector: "#email", value: "test@example.com" }
      - { action: "fill", selector: "#message", value: "Automated test message" }
      - { action: "screenshot", name: "form_filled" }
      - { action: "click", selector: "button[type=submit]" }
      - { action: "wait_for", selector: ".success-message", timeout_ms: 5000 }
      - { action: "screenshot", name: "form_submitted" }

  - name: "Services page"
    steps:
      - { action: "goto", url: "/services" }
      - { action: "screenshot", name: "services" }
      - { action: "click", selector: "a.service-card:first-child" }
      - { action: "wait_for", selector: ".service-detail" }
      - { action: "screenshot", name: "service_detail" }

  - name: "Mobile navigation"
    viewports: ["mobile"]           # Override: only run on mobile
    steps:
      - { action: "goto", url: "/" }
      - { action: "click", selector: ".hamburger-menu" }
      - { action: "wait_for", selector: ".mobile-nav.open" }
      - { action: "screenshot", name: "mobile_nav" }
      - { action: "assert_visible", selector: ".mobile-nav a[href='/contact']" }
```

### Supported Actions

| Action | Description | Parameters |
|---|---|---|
| `goto` | Navigate to URL | `url` (relative to base_url) |
| `click` | Click an element | `selector` |
| `fill` | Type into an input | `selector`, `value` |
| `wait_for` | Wait for element to appear | `selector`, `timeout_ms` (default 10000) |
| `screenshot` | Capture screenshot | `name` (used in filename) |
| `assert_visible` | Assert element is visible | `selector` |
| `assert_text` | Assert element contains text | `selector`, `contains` or `equals` |
| `assert_hidden` | Assert element is NOT visible | `selector` |
| `hover` | Hover over element | `selector` |
| `select` | Select dropdown option | `selector`, `value` |
| `if_exists` | Conditional — run nested steps only if element exists | `selector`, `steps` |
| `sleep` | Wait a fixed duration | `ms` |

### Environment Variable Substitution

Values prefixed with `${...}` are resolved from environment variables. This keeps secrets out of YAML files:

```yaml
- { action: "fill", selector: "#password", value: "${BOBS_ADMIN_PASSWORD}" }
```

---

## How Logins Are Handled

Three strategies, configured per-app in YAML:

### 1. Form-based login (most common)
Define login steps in the `auth` section. Playwright executes them before running test flows, then saves the browser state (`storageState`) so subsequent flows are already authenticated.

### 2. Cookie/token injection
For apps using JWT or session cookies:
```yaml
auth:
  type: "cookie"
  cookies:
    - { name: "session", value: "${BOBS_SESSION_TOKEN}", domain: "bobsplumbing.com" }
```

### 3. No auth
Public-facing pages that don't need login — just omit the `auth` section.

**Auth state is persisted per-run:** Playwright's `storageState` (cookies + localStorage) is saved after auth and reused for all flows in the same run. This means login only happens once per test run, not per flow.

---

## Multi-Step Workflow Definition

Workflows are just sequences of steps in YAML (shown above). Key design decisions:

- **No branching/loops** — test flows are linear sequences. If you need conditional behavior, use `if_exists` for optional elements (e.g., dismiss cookie banner if present).
- **Flows are independent** — each flow starts fresh (new page context, but reuses auth state). A failure in one flow doesn't affect others.
- **Viewport-per-flow** — by default every flow runs on every configured viewport. Use `viewports: ["mobile"]` on a flow to restrict it.
- **Fail fast per step** — if a step fails (element not found, assertion fails), that flow is marked failed with a screenshot of the current state, and the runner moves to the next flow.

---

## Visual Regression (Pixel-Diff)

Screenshots are compared against stored baselines:

1. **First run:** All screenshots become the baseline
2. **Subsequent runs:** Each screenshot is compared pixel-by-pixel against baseline
3. **Diff threshold:** Configurable (default 0.5% pixel difference). Accounts for anti-aliasing, font rendering, etc.
4. **On diff detected:** Stores the new screenshot, the baseline, and a diff image highlighting changes
5. **Accept/reject:** James can accept a new baseline via intent ("accept screenshot baseline for bob's plumbing homepage desktop")

**Implementation:** Pillow `ImageChops.difference()` — zero external dependencies, zero LLM cost.

---

## Storage

### PostgreSQL Tables

```sql
-- Test flow configurations (which apps/flows are registered)
app_test_configs (
    id SERIAL PRIMARY KEY,
    config_id UUID UNIQUE NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,                    -- "Bob's Plumbing Website"
    yaml_path TEXT NOT NULL,               -- "data/test_flows/bobs_plumbing.yaml"
    schedule_hours INT DEFAULT 24,         -- Run every N hours (0 = manual only)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)

-- Individual test runs
app_test_runs (
    id SERIAL PRIMARY KEY,
    run_id UUID UNIQUE NOT NULL,
    config_id UUID REFERENCES app_test_configs(config_id),
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,                  -- 'running', 'passed', 'failed', 'error'
    triggered_by TEXT NOT NULL,            -- 'schedule', 'deploy', 'manual'
    commit_sha TEXT,                       -- From dev_watcher
    branch TEXT,
    total_flows INT DEFAULT 0,
    passed_flows INT DEFAULT 0,
    failed_flows INT DEFAULT 0,
    duration_ms INT DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
)

-- Per-flow results within a run
app_test_flow_results (
    id SERIAL PRIMARY KEY,
    result_id UUID UNIQUE NOT NULL,
    run_id UUID REFERENCES app_test_runs(run_id),
    flow_name TEXT NOT NULL,
    viewport TEXT NOT NULL,                -- "desktop", "tablet", "mobile"
    status TEXT NOT NULL,                  -- 'passed', 'failed', 'error'
    error_message TEXT,
    failed_step INT,                       -- Index of the step that failed
    duration_ms INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
)

-- Screenshots (metadata only — actual images on filesystem)
app_test_screenshots (
    id SERIAL PRIMARY KEY,
    screenshot_id UUID UNIQUE NOT NULL,
    result_id UUID REFERENCES app_test_flow_results(result_id),
    run_id UUID REFERENCES app_test_runs(run_id),
    name TEXT NOT NULL,                    -- "homepage_desktop"
    file_path TEXT NOT NULL,               -- Relative path in data/screenshots/
    is_baseline BOOLEAN DEFAULT FALSE,
    diff_pct FLOAT,                        -- Pixel difference from baseline
    diff_path TEXT,                         -- Path to diff image
    viewport TEXT NOT NULL,
    width INT,
    height INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

### Filesystem Layout

```
data/
  test_flows/                         # YAML flow definitions
    bobs_plumbing.yaml
    sarahs_salon.yaml
  screenshots/                        # Captured screenshots
    baselines/                        # Accepted baseline images
      bobs_plumbing/
        homepage_desktop.png
        homepage_mobile.png
    runs/                             # Per-run screenshots
      2026-02-10_abc123/
        homepage_desktop.png
        homepage_desktop_diff.png     # Visual diff overlay
        form_filled_mobile.png
```

### Auto-Cleanup

On each heartbeat (L3 frequency, ~daily):
- Delete run screenshots older than `retention_days` (default 30)
- Keep baselines indefinitely
- Delete PostgreSQL rows for expired runs
- Log cleanup summary

---

## Skill Intents

| Intent | Description | Triggered by |
|---|---|---|
| `run_app_tests` | Execute test flows for an app | Manual, dev_watcher, heartbeat |
| `list_test_configs` | Show registered test configurations | Manual |
| `get_test_results` | Get results for a specific run or latest | Manual |
| `get_test_summary` | Portfolio summary — all apps, latest status | Manual |
| `add_test_config` | Register a new app for testing | Manual |
| `update_test_config` | Change schedule, viewports, etc. | Manual |
| `accept_baseline` | Accept a new screenshot as baseline | Manual |
| `remove_test_config` | Unregister an app | Manual |

---

## Files to Create/Modify

### New Files

| File | Purpose |
|---|---|
| `src/zetherion_ai/skills/app_tester.py` | Main skill — orchestration, intents, heartbeat, cleanup |
| `src/zetherion_ai/skills/app_tester_runner.py` | Playwright execution engine — runs flows, captures screenshots |
| `src/zetherion_ai/skills/app_tester_diff.py` | Visual regression — pixel-diff comparison using Pillow |
| `src/zetherion_ai/skills/app_tester_models.py` | Dataclasses — TestConfig, TestRun, FlowResult, Screenshot |
| `tests/unit/test_skill_app_tester.py` | Unit tests for skill orchestration |
| `tests/unit/test_app_tester_runner.py` | Unit tests for flow execution (mocked Playwright) |
| `tests/unit/test_app_tester_diff.py` | Unit tests for pixel-diff logic |
| `Dockerfile.test-runner` | Docker image with Playwright + Chromium |
| `data/test_flows/.gitkeep` | Directory for YAML flow definitions |

### Modified Files

| File | Change |
|---|---|
| `src/zetherion_ai/skills/server.py` | Register `ClientAppTesterSkill` (conditional on config) |
| `src/zetherion_ai/config.py` | Add `app_tester_enabled`, `app_tester_runner_url`, `app_tester_screenshot_dir`, `app_tester_retention_days` |
| `.env.example` | Add new env vars |
| `docker-compose.yml` | Add `zetherion-test-runner` service |
| `docker-compose.test.yml` | Add test runner for integration tests |
| `tests/unit/test_skills_server_main.py` | Update registration count if needed |
| `pyproject.toml` | Add `Pillow` and `pyyaml` dependencies |

### Key Existing Code to Reuse

| What | Where | Reuse for |
|---|---|---|
| Skill base class + metadata | `src/zetherion_ai/skills/base.py` | Inherit from `Skill` |
| HeartbeatAction pattern | `src/zetherion_ai/skills/dev_watcher.py` | Schedule periodic runs, send summaries |
| PostgreSQL manager | `src/zetherion_ai/api/tenant.py` | Schema creation pattern for test tables |
| Notification dispatcher | `src/zetherion_ai/notifications/dispatcher.py` | Alert on test failures |
| Docker service pattern | `docker-compose.yml` | Model test-runner service on existing services |
| Permission model | `src/zetherion_ai/skills/permissions.py` | SEND_MESSAGES, WRITE_OWN_COLLECTION, READ_CONFIG |

---

## Test Runner Service (Separate Container)

### `Dockerfile.test-runner`

```dockerfile
FROM python:3.12-slim

RUN pip install playwright aiohttp pyyaml Pillow
RUN playwright install chromium --with-deps

COPY src/zetherion_ai/skills/app_tester_runner.py /app/runner.py
COPY src/zetherion_ai/skills/app_tester_diff.py /app/diff.py

WORKDIR /app
EXPOSE 9090
CMD ["python", "-m", "aiohttp.web", "-H", "0.0.0.0", "-P", "9090", "runner:create_app"]
```

### Runner API (internal, port 9090)

| Endpoint | Purpose |
|---|---|
| `POST /run` | Execute a test flow YAML, return results + screenshot paths |
| `GET /health` | Health check |

The main skill sends the YAML content + config to the runner via HTTP. The runner executes Playwright, saves screenshots to a shared volume, and returns results.

### `docker-compose.yml` addition

```yaml
zetherion-test-runner:
  build:
    context: .
    dockerfile: Dockerfile.test-runner
  container_name: zetherion-test-runner
  restart: unless-stopped
  volumes:
    - ./data/screenshots:/app/data/screenshots
    - ./data/test_flows:/app/data/test_flows
  environment:
    - RUNNER_HOST=0.0.0.0
    - RUNNER_PORT=9090
  networks:
    - zetherion-ai-net
  deploy:
    resources:
      limits:
        cpus: '2.0'
        memory: 2G          # Chromium is memory-hungry
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')"]
    interval: 30s
    timeout: 10s
    retries: 3
```

---

## Dev Watcher Integration

Dev watcher triggers the app tester in two scenarios:

### 1. On Deploy (app code changes)

When dev_watcher detects a deploy (commit to main, tag push, etc.), it checks whether the repo is associated with a registered test config. If so, it fires a HeartbeatAction to trigger an immediate test run:

```python
HeartbeatAction(
    skill_name="client_app_tester",
    action_type="run_on_deploy",
    user_id=user_id,
    data={"config_id": "...", "commit_sha": "abc123", "branch": "main"},
    priority=5,
)
```

### 2. On YAML Flow Change (test definition changes)

When dev_watcher detects that a YAML flow file has been added or modified (either committed to a repo or changed on the filesystem in `data/test_flows/`), it triggers:

1. **Re-validation** — the app tester re-parses the YAML to catch syntax errors early
2. **Immediate test run** — executes the updated flows against the target app
3. **Baseline handling** — any *new* screenshot names (not previously seen) auto-baseline on first capture. Existing screenshot names keep their current baselines so you can see whether the old flows still pass under the new definition.

```python
HeartbeatAction(
    skill_name="client_app_tester",
    action_type="run_on_flow_change",
    user_id=user_id,
    data={
        "config_id": "...",
        "yaml_path": "data/test_flows/bobs_plumbing.yaml",
        "changed_flows": ["Contact form submission"],  # Which flows changed
    },
    priority=5,
)
```

**How dev_watcher detects YAML changes:**
- If the YAML lives in a watched Git repo: detected via commit diff (file path matches `*.yaml` in the test_flows directory)
- If the YAML lives locally in `data/test_flows/`: dev_watcher monitors the directory on each heartbeat by checking file mtimes against a stored cache. Changed files trigger the flow change action.

This means you can either version control your YAML flows alongside app code (recommended) or edit them locally — both paths trigger re-testing automatically.

---

## Implementation Phases

### Phase 1: Foundation — Skill Shell + Models + PostgreSQL Schema
- Create `app_tester_models.py` with dataclasses (TestConfig, TestRun, FlowResult, Screenshot)
- Create `app_tester.py` skill shell with metadata, intents, PostgreSQL table creation
- Add config fields to `config.py`
- Register in `server.py`
- Unit tests for models and skill initialization
- **Deliverable:** Skill registers, initializes, creates tables. Intents return "not yet implemented".

### Phase 2: YAML Parser + Flow Runner (Mocked)
- Create `app_tester_runner.py` with YAML parsing, flow execution engine, env var substitution
- All Playwright calls are async methods that can be easily mocked
- Unit tests with mocked Playwright (no actual browser needed)
- **Deliverable:** Flows parse correctly, steps execute in order, failures are captured. All tested without Playwright.

### Phase 3: Visual Diff Engine
- Create `app_tester_diff.py` — baseline management, pixel-diff with Pillow
- `compare_screenshots(baseline_path, new_path, threshold=0.005) -> DiffResult`
- `accept_baseline(screenshot_id)` — promote a screenshot to baseline
- Unit tests with synthetic test images
- **Deliverable:** Screenshots can be compared, diffs generated, baselines managed.

### Phase 4: Docker Test Runner
- Create `Dockerfile.test-runner` with Playwright + Chromium
- Create the aiohttp runner service (receives YAML, runs Playwright, returns results)
- Wire up the main skill to call the runner via HTTP
- Add to `docker-compose.yml`
- Integration tests (skill → runner → actual browser)
- **Deliverable:** End-to-end test execution works in Docker.

### Phase 5: Scheduling + Dev Watcher + Cleanup
- Implement `on_heartbeat()` — periodic test runs based on schedule_hours
- Implement dev_watcher integration — trigger tests on deploy
- Implement auto-cleanup — delete old screenshots and run records
- Implement heartbeat notifications — send test summaries to James
- **Deliverable:** Tests run automatically on schedule and deploy. Old data is cleaned up.

---

## Verification

After each phase:
1. `ruff check src/ tests/` passes
2. `pytest tests/ -m "not integration and not discord_e2e"` passes
3. Existing tests remain green

End-to-end (after Phase 5):
1. Register a test config with YAML flow
2. Trigger a manual test run → all flows execute → screenshots captured
3. Wait for heartbeat → periodic run triggers automatically
4. Simulate a deploy event → on-deploy run triggers
5. Check visual diff against baseline
6. Verify auto-cleanup removes old screenshots
7. Verify James receives test summary notifications

## Dependencies to Add

- `Pillow` — Image comparison for visual regression
- `pyyaml` — YAML flow definition parsing
- `playwright` — Browser automation (test-runner container only, not main skill)
