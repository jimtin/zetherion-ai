# AI Agent Integration Guide

## Why Zetherion Exists

Zetherion is built as an execution layer for AI agents that need to do more
than answer questions. It combines:

- multi-provider inference routing (local + cloud),
- tenant/user-scoped memory and state,
- policy-aware action routing (email/tasks/calendar),
- and production-facing APIs for app integration.

The practical story is simple: use Zetherion when you want an AI system that
can reason, remember, and act across real workflows without giving up
boundaries like tenancy, authentication, and security gating.

---

## Integration Surfaces

| Surface | Audience | Auth | Purpose |
|---|---|---|---|
| Public API (`:8443`) | External apps/websites | `X-API-Key` + Bearer session token | Tenant chat sessions, chat history, streaming responses, YouTube tenant routes |
| Skills API (`:8080`, internal) | Trusted internal services | `X-API-Secret` | Skill execution, heartbeat actions, OAuth callbacks, runtime settings/secrets |
| Discord bot gateway | Human operators | Discord identity + allowlist/RBAC | Natural-language control plane for skills and operations |

---

## Public API Contract (App Integrations)

Base path: `/api/v1`

### Auth model

1. `X-API-Key` for tenant-scoped control calls (session creation, YouTube routes).
2. Bearer session token for chat calls (`Authorization: Bearer zt_sess_...`).
3. Session tokens are JWT-based and default to 24-hour expiry.

### Core flow (recommended)

1. Create session (server side):
   `POST /api/v1/sessions` with `X-API-Key`.
2. Receive `session_token`.
3. Use token from frontend/backend for chat:
   `POST /api/v1/chat` or `POST /api/v1/chat/stream`.
4. Optional history retrieval:
   `GET /api/v1/chat/history?limit=50`.

### Chat behaviors

- Max message size: 10,000 chars.
- History default page size: 50, max 100.
- Streaming uses SSE with `token` and `done` events.
- If inference is unavailable, API returns safe fallback text instead of hard-crashing.

### Tenant rate limits

- Per-tenant in-memory token bucket.
- Default limit: 60 requests/minute (configurable per tenant).
- Over limit: HTTP `429` with retry hint.

---

## YouTube API Surface (Tenant-Scoped)

When YouTube storage/skills are enabled, the Public API also exposes tenant
routes under `/api/v1/youtube`, including:

- channel registration/listing,
- data ingestion (videos/comments/stats/documents),
- intelligence reports and history,
- management configuration/reply review,
- strategy generation/history,
- assumptions list/update/validation.

All routes enforce tenant ownership checks before returning or mutating data.

---

## Skills API Contract (Internal Control Plane)

Base path: `/`

Primary endpoints:

- `POST /handle` for direct skill execution.
- `POST /heartbeat` for periodic proactive actions.
- `GET /skills`, `GET /skills/{name}`, `GET /intents`, `GET /status`.
- `GET /oauth/{provider}/authorize`, `GET /oauth/{provider}/callback`.
- `GET/PUT/DELETE /settings/{namespace}/{key}`.
- `GET/PUT/DELETE /secrets...` for encrypted secret management.

This API is designed for trusted network contexts and is not the public
internet integration path.

---

## What Zetherion Can Do

1. Run tenant-isolated chat for external products with sessionized auth.
2. Route inference across local/cloud models by task type and availability.
3. Execute provider-agnostic email/task/calendar routing via normalized models.
4. Enforce a security gate before automated routing writes.
5. Detect calendar conflicts across multiple connected calendars.
6. Operate with progressive autonomy modes (`auto`, `ask`, `draft`, `review`, `block`).
7. Persist structured user and tenant intelligence in PostgreSQL.
8. Expose deterministic operational APIs for settings, skills, and integrations.

---

## CRM and Tenant Intelligence (Important)

Zetherion has a real CRM-oriented intelligence layer that was under-described in
the first draft of this guide.

### Tenant CRM intelligence pipeline

1. `client_chat` handles runtime tenant conversations and inline critical signals.
2. `tenant_intelligence` performs asynchronous extraction:
   - per-message entities (contact, intent, sentiment, purchase signals),
   - per-session summaries (outcome, topics, unmet needs, follow-up actions).
3. Extracted data is persisted into tenant-scoped CRM tables:
   - `tenant_contacts`,
   - `tenant_interactions`.

### Portfolio intelligence for operators

`client_insights` aggregates tenant interaction data into owner-facing portfolio
signals:

- client health indicators,
- escalation-rate alerts,
- cross-tenant trend analysis and recommendations,
- heartbeat-driven proactive notifications.

### Tenant lifecycle operations

`client_provisioning` supports tenant creation/configuration/deactivation and
API key rotation for managed client environments.

---

## Personal Profiling and Adaptive Behavior (Important)

Zetherion also includes a user-level personal understanding model:

1. Profile state:
   identity, timezone/locale, goals, communication style, working hours.
2. Relationship graph:
   contact records with relationship type, importance, and interaction history.
3. Policy model:
   per-domain autonomy policies (`AUTO`, `DRAFT`, `ASK`, `NEVER`) with trust scores.
4. Learning memory:
   explicit/inferred/email/calendar/discord learnings with category, confidence,
   and confirmation status.

This model powers adaptive behavior (how the assistant responds and when it can
act) and exposes management intents such as summary/update/forget/export/policy
inspection.

---

## Observation-Driven Learning

Beyond direct commands, Zetherion has a tiered observation pipeline:

1. Ingest observation events from conversation and integration sources.
2. Run Tier 1/2/3 extraction and merge/deduplicate results.
3. Dispatch structured outputs to action targets and memory/policy systems.
4. Route email-origin observations through the shared provider-agnostic email
   router when configured.

This is how the system accumulates context over time instead of acting as a
stateless chat endpoint.

---

## What Zetherion Does Not Do

1. It does not provide anonymous/public chat access without tenant credentials.
2. It does not expose tenant provisioning as a public endpoint on `/api/v1`
   (provisioning is an internal/operator workflow).
3. It does not treat flagged/blocked malicious routing content as auto-actionable.
4. It does not provide native WebSocket chat transport; streaming is SSE.
5. It does not fully enable Outlook parity by default (Outlook adapter is feature-flagged scaffold).
6. It does not process email attachments in routing by default
   (`attachment_handling_enabled=False`).
7. It does not allow cross-tenant data reads in normal API flows.

---

## Operational Capabilities Often Missed

1. Runtime settings and secret rotation without service restart
   (`/settings`, `/secrets` on Skills API).
2. OAuth provider authorization/callback orchestration from the Skills service.
3. Integration ingestion queue + dead-letter handling for fail-closed router
   dependency outages.
4. Health analysis and anomaly-driven self-healing recommendations.
5. Model/provider cost tracking and report generation.

---

## Exposure Boundaries (Critical for Integrators)

Some capabilities are implemented but not yet first-class public API resources:

1. Tenant provisioning workflows are internal skill/control-plane operations.
2. CRM extraction outputs are persisted and available via tenant-scoped public
   read endpoints (`/api/v1/crm/*` and `/api/v1/analytics/funnel`) using
   API-key auth.
3. Personal profile and policy controls are skill-facing; external app exposure
   should be mediated through trusted service layers.

---

## Capability Gates You Must Model In Your Agent

When integrating from another agent framework, assume these gates:

1. Auth gate:
   no valid key/token means no action.
2. Security gate:
   malicious content can force `block` or `review`.
3. Policy gate:
   conflict and route policy can downgrade from `auto` to `ask`/`draft`.
4. Dependency gate:
   router/model unavailability can queue work instead of executing immediately.

Agent integrations should be built to handle these outcomes explicitly instead
of assuming one-shot success.

---

## Recommended Integration Patterns

### Pattern A: Embedded customer chat in a SaaS app

1. Backend stores tenant API key (never expose to browser).
2. Backend mints session via `/api/v1/sessions`.
3. Frontend uses Bearer session token for `/chat` or `/chat/stream`.
4. Frontend handles `401`, `403`, `429`, and stream completion events.

### Pattern B: Internal automation orchestrator

1. Run inside trusted network.
2. Use Skills API `/handle` for deterministic skill requests.
3. Use `/heartbeat` for scheduled task collection.
4. Manage runtime OAuth/settings/secrets via settings/secrets endpoints.

### Pattern C: Email-to-work routing

1. Enable `WORK_ROUTER_ENABLED=true`.
2. Connect provider accounts (Google fully wired; Outlook optional/scaffold).
3. Route unread with `email_route`.
4. Handle non-auto outcomes (`ask`, `draft`, `review`, queued-unavailable).

---

## Minimal Public API Example

```bash
# 1) Create a session
curl -sS -X POST "http://localhost:8443/api/v1/sessions" \
  -H "X-API-Key: sk_live_..." \
  -H "Content-Type: application/json" \
  -d '{"external_user_id":"user-42","metadata":{"app":"web-chat"}}'

# 2) Send chat message
curl -sS -X POST "http://localhost:8443/api/v1/chat" \
  -H "Authorization: Bearer zt_sess_..." \
  -H "Content-Type: application/json" \
  -d '{"message":"I need help with my order"}'
```

---

## Operational Requirements

For reliable cross-app integration, ensure:

1. `API_JWT_SECRET` is set (required for public API sessions).
2. PostgreSQL is reachable for tenant/session/message persistence.
3. Inference providers are configured for your expected workloads.
4. If using work routing:
   `WORK_ROUTER_ENABLED=true` plus provider credentials.
5. If using internet exposure:
   Cloudflare tunnel and API host/port are configured.

---

## Positioning For Other AI Agents

Use this framing when presenting Zetherion to agent systems:

"Zetherion is a policy-aware AI operations layer. It gives agents a secure way
to talk, remember, classify, and act across tenant-scoped workflows using
deterministic APIs and explicit action modes. It is not just a model wrapper;
it is an execution fabric with guardrails."

---

## Source Pointers

- Public API server: `src/zetherion_ai/api/server.py`
- Public API auth/middleware: `src/zetherion_ai/api/auth.py`, `src/zetherion_ai/api/middleware.py`
- Tenant/session/chat persistence: `src/zetherion_ai/api/tenant.py`
- Chat routes: `src/zetherion_ai/api/routes/chat.py`
- YouTube API routes: `src/zetherion_ai/api/routes/youtube.py`
- Tenant provisioning skill: `src/zetherion_ai/skills/client_provisioning.py`
- Tenant CRM intelligence skill: `src/zetherion_ai/skills/tenant_intelligence.py`
- Portfolio insights skill: `src/zetherion_ai/skills/client_insights.py`
- Skills server/control plane: `src/zetherion_ai/skills/server.py`
- Provider-agnostic email router: `src/zetherion_ai/routing/email_router.py`
- Task/calendar router: `src/zetherion_ai/routing/task_calendar_router.py`
- Routing models/contracts: `src/zetherion_ai/routing/models.py`
- Personal model skill: `src/zetherion_ai/skills/personal_model.py`
- Personal model schema/models: `src/zetherion_ai/personal/models.py`
- Observation pipeline: `src/zetherion_ai/observation/pipeline.py`
- Integration storage/queueing: `src/zetherion_ai/integrations/storage.py`
