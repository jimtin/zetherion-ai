# Plan: Multi-Tenant Public API + Client Intelligence Skills

## Context

Zetherion AI currently runs as a self-hosted Discord bot on a home server. The goal is to expose it as a backend service for multiple client websites (chatbots, CRM, etc.). This involves two parallel workstreams:

1. **Workstream A: Public API Infrastructure** — The plumbing to expose Zetherion to the internet
2. **Workstream B: Client Intelligence Skills** — The brains that extract multi-level intelligence from conversations

**Key decisions:**
- Networking: Cloudflare Tunnel (no open ports)
- Auth: Per-site API key + lightweight session tokens
- Chat UX: Streaming via SSE
- Clients have their own backends (API keys stay server-side)
- Shared infrastructure, tenant-isolated data via `tenant_id`
- All skills are platform-agnostic (Discord today, Slack/web/etc. tomorrow)
- CRM is a separate skill workstream, not part of the API infrastructure

## The Multi-Level Intelligence Model

When a customer chats on a client's website, Zetherion operates at multiple levels simultaneously:

```
End-user sends message on Bob's Plumbing site
  → L1a INLINE: Critical signal detection (urgency, safety, escalation)
      → Bot adjusts response if needed ("I can see this is urgent...")
  → client_chat generates a response for the end-user
  → L1b ASYNC: Entity extraction (contact info, intent, sentiment)
  → L2  ASYNC: Session summary when conversation ends
  → L3  PERIODIC: Daily/weekly tenant aggregation
  → L4  PERIODIC: Cross-tenant intelligence (James only)
  → L5  CONTINUOUS: Feedback loop (bot improvement suggestions)
```

This is the key differentiator from traditional CRMs/chatbot platforms — Zetherion serves both the tenant AND the owner simultaneously, with different extraction lenses.

### Intelligence Extraction Matrix

**Level 1a: Per-Message Critical Signals (INLINE, pre-response)**
Lightweight detection that runs before the bot responds, so the response can adapt.

| Signal | For Tenant | For James | Example |
|--------|-----------|-----------|---------|
| Urgency detection | Bot reacts ("flagging for callback") | Alert if critical | "My pipe burst and water is flooding" |
| Safety/harm signals | Bot escalates appropriately | Immediate alert | Self-harm indicators, threats |
| Escalation triggers | Route to human if configured | Track escalation rate | "I want to speak to a real person" |
| Returning customer | Bot can greet by name | -- | Session matches previous contact |

**Level 1b: Per-Message Entity Extraction (ASYNC, post-response)**
Heavier LLM-based extraction that doesn't slow down the response.

| Signal | For Tenant | For James | Example |
|--------|-----------|-----------|---------|
| Contact entities (name, email, phone) | Store in tenant CRM | -- | "My name is Dave, call me on 07700..." |
| Intent classification | Tag the interaction | -- | Enquiry / complaint / booking / support |
| Sentiment score | Track per-customer | Feed into aggregation | Frustrated / neutral / happy |
| Product/service mentioned | Tag interaction | -- | "bathroom renovation", "boiler service" |
| Purchase signals | Flag as lead | -- | "How much would it cost to..." |
| Communication preferences | CRM enrichment | -- | "I prefer email" / "Don't call before 10am" |

**Level 2: Per-Session Summary (ASYNC, on session close/timeout)**

| Signal | For Tenant | For James | Example |
|--------|-----------|-----------|---------|
| Conversation outcome | Resolved / unresolved / needs followup | Track resolution rates | Did the chatbot actually help? |
| Customer profile summary | CRM enrichment | -- | "Price-sensitive homeowner, kitchen refit, prefers weekends" |
| Unmet needs / opportunities | Opportunity alert ("customers want X, you don't offer it") | -- | "Asked about gas safety certs — Bob doesn't list this" |
| Sentiment trajectory | Customer satisfaction trend | Bot technical performance (did the bot cause frustration? misunderstand? fail to answer?) | Started frustrated → ended satisfied |
| Chatbot effectiveness | -- | Technical quality signal (response failures, hallucinations, missed intents) | Bot couldn't answer 3 questions → config needs work |
| Topics covered | FAQ data + content gap detection ("customers keep asking about X") | Visibility into tenant content gaps (read-only) | Which questions come up repeatedly? |
| Follow-up actions needed | Task creation for tenant | -- | "Customer wants a callback Tuesday" |

**Level 3: Per-Tenant Aggregation (PERIODIC, daily/weekly rollup)**
Tenants can access their own L3 data via API.

| Signal | For Tenant | For James | Example |
|--------|-----------|-----------|---------|
| Volume trends | "45 enquiries this week (+20%)" | Engagement health | Is this client's site being used? |
| Top topics / FAQs | "Customers mostly ask about pricing" | Cross-client patterns | Common across plumbing clients? |
| Avg sentiment over time | Customer satisfaction trend | Client health score | Sentiment dropping → something wrong |
| Conversion rate | "12/45 conversations were purchase-intent" | Revenue signal | High conversion = valuable client |
| Peak hours | "Most chats happen 6-9pm" | -- | Useful for tenant's staffing |
| Unmet needs (aggregated) | "X asked about 15 times — you don't offer it" | Upsell opportunity | "Suggest Bob adds emergency plumbing" |
| Repeat visitor rate | Loyalty signals | -- | Returning customers = good sign |
| Escalation rate | "8% needed human handoff" | Bot quality signal | High escalation = config needs work |
| Response quality score | "Bot resolved 85% without escalation" | Service quality metric | Benchmarkable across clients |

**Level 4: Cross-Tenant Intelligence (PERIODIC, weekly, James only)**
Privacy boundaries: TBD — to be decided whether James sees anonymized aggregates or named comparisons.

| Signal | For James | Example |
|--------|-----------|---------|
| Industry benchmarks | "Bob converts at 26%, trade avg is 18%" |
| Best-performing configs | "Formal tone converts better for trade services" |
| Emerging patterns | "3 clients getting AI-related questions — new service area?" |
| Client health dashboard | Red/amber/green portfolio overview |
| Churn risk | "Dave's site usage dropped 60% — check in" |
| Revenue attribution | Which clients generate most value vs effort? |

**Level 5: Feedback Loop (CONTINUOUS, threshold-triggered)**

| Signal | For James | Example |
|--------|-----------|---------|
| Bot improvement suggestions | "Bob's bot fails on pricing questions — add FAQ to config" |
| Knowledge gaps | "Customers ask about X and the bot doesn't know" |
| Prompt tuning signals | "Sarah's bot is too verbose — customers drop off mid-response" |
| Config recommendations | "Based on successful clients, try adjusting tone to..." |

### Extraction Timing Summary

| Level | Trigger | Latency | Storage | Tenant can see? |
|-------|---------|---------|---------|----------------|
| L1a | Inline pre-response | <100ms | In-memory (flags) | No (internal) |
| L1b | Async post-response | Seconds | Tenant Postgres | Yes |
| L2 | Session close/timeout | Seconds | Tenant Postgres + James's Qdrant | Yes |
| L3 | Daily/weekly cron | Background | Aggregation tables | Yes (via API) |
| L4 | Weekly cron | Background | James's Qdrant | No (James only) |
| L5 | Threshold triggers | Background | Triggers notifications | No (James only) |

## Architecture

```
Internet                     Home Server Docker Network
  |                          +------------------------------------+
  |   Cloudflare Tunnel      |                                    |
  +----> cloudflared --------+--> zetherion-api (8443) [NEW]      |
                             |        |                           |
                             |        v                           |
                             |   zetherion-ai-skills (8080)       |
                             |        |                           |
                             |   +----+-----+                     |
                             |   |          |                     |
                             |  postgres  qdrant   ollama         |
                             |                                    |
Discord/Slack/etc.           |                                    |
  +----> zetherion-ai-bot ---+--> zetherion-ai-skills (8080)      |
                             +------------------------------------+
```

## Auth Flow

```
1. Client backend creates a session (server-to-server):
   POST /api/v1/sessions  [X-API-Key: sk_live_...]
   → { session_token: "zt_sess_..." }

2. Client passes session_token to their frontend

3. Browser sends chat messages using session token:
   POST /api/v1/chat  [Authorization: Bearer zt_sess_...]
   → SSE stream of AI response tokens
```

API keys: `sk_live_` prefix, bcrypt-hashed. Session tokens: signed JWTs (24hr expiry).

---

## Workstream A: Public API Infrastructure

### Phase A1: Foundation — Tenant Management & API Skeleton

**New files:**
- `src/zetherion_ai/api/__init__.py`
- `src/zetherion_ai/api/server.py` — Public aiohttp app (port 8443), modeled on `skills/server.py`
- `src/zetherion_ai/api/tenant.py` — `TenantManager` (CRUD, API key gen/validation), modeled on `discord/user_manager.py`
- `src/zetherion_ai/api/auth.py` — API key generation (`secrets.token_urlsafe` + bcrypt), JWT session tokens (`PyJWT`)
- `src/zetherion_ai/api/middleware.py` — CORS, API key validation, rate limiting, tenant context injection
- `src/zetherion_ai/api/models.py` — Pydantic request/response schemas
- `src/zetherion_ai/api/routes/__init__.py`
- `src/zetherion_ai/api/routes/health.py` — `GET /api/v1/health` (no auth)
- `src/zetherion_ai/api/routes/sessions.py` — Session CRUD (requires API key)

**Modified files:**
- `src/zetherion_ai/config.py` — Add `api_host`, `api_port`, `api_jwt_secret`
- `.env.example` — Add new env vars

**Database (new tables):**
```sql
tenants (id, tenant_id UUID, name, domain, api_key_hash, api_key_prefix,
         is_active, rate_limit_rpm, allowed_skills[], config JSONB, timestamps)

chat_sessions (id, session_id UUID, tenant_id FK, external_user_id,
               metadata JSONB, created_at, last_active, expires_at)
```

**Tests:** `test_api_auth.py`, `test_api_tenant.py`, `test_api_http.py`

**Deliverable:** API starts, health check works, API key auth works, sessions can be created.

### Phase A2: Chat Endpoint — REST Chat with AI

**New files:**
- `src/zetherion_ai/api/routes/chat.py` — `POST /api/v1/chat`, `GET /api/v1/chat/history`

**Modified files:**
- `src/zetherion_ai/agent/core.py` — Add `generate_response_for_tenant()` accepting `tenant_id` + `session_id` instead of Discord-specific IDs. Uses tenant config for system prompt overrides. Scopes Qdrant by `tenant_id`.

**Database:**
```sql
chat_messages (id, session_id FK, tenant_id, role, content, metadata JSONB, created_at)
```

**Integration:** Chat route calls `Agent` via `SkillsClient` (internal HTTP to 8080), using `user_id=f"tenant:{tenant_id}:session:{session_id}"` for data isolation.

**Deliverable:** POST a message, get an AI response. History persists.

### Phase A3: SSE Streaming

**Modified files:**
- `src/zetherion_ai/api/routes/chat.py` — Add SSE endpoint using `aiohttp.web.StreamResponse`
- `src/zetherion_ai/agent/core.py` — Streaming variant that yields tokens

**SSE format:**
```
data: {"type": "token", "content": "Hello"}
data: {"type": "token", "content": " there"}
data: {"type": "done", "message_id": "..."}
```

**Deliverable:** Real-time streaming responses.

### Phase A4: Cloudflare Tunnel & Production Hardening

**New files:**
- `Dockerfile.api` — Multi-stage build (same Chainguard pattern as `Dockerfile.skills`)

**Modified files:**
- `docker-compose.yml` — Add `zetherion-api` + `cloudflared` services
- `.env.example` — Add `CLOUDFLARE_TUNNEL_TOKEN`
- `scripts/pre-push-tests.sh` — Add API server tests

**Hardening:** Rate limiting, request size limits, audit logging (`tenant_audit_log`), usage tracking.

**Deliverable:** API accessible via `https://api.yourdomain.com`.

---

## Workstream B: Client Intelligence Skills

These are platform-agnostic skills. They can be triggered from Discord, Slack, the public API, or any future interface. Notifications go through a dispatch layer (Discord DM today, Slack/webhook tomorrow).

### Skill B1: `client_provisioning` — Setup & Lifecycle

**Purpose:** Create and manage client tenants. James triggers this from any interface.

**Intents:**
- `client_create` — "Set up a new client called Bob's Plumbing for bobsplumbing.com"
- `client_configure` — "Update Bob's chatbot personality to be more formal"
- `client_deactivate` — "Pause Bob's account"
- `client_rotate_key` — "Generate a new API key for Bob"
- `client_list` — "Show me all my clients"

**Implementation:**
- New skill in `src/zetherion_ai/skills/client_provisioning.py`
- Calls `TenantManager` (from Workstream A) for all CRUD
- Registers with `SkillRegistry` like any other skill
- Platform-agnostic: works via Discord command, Slack, or future admin dashboard

### Skill B2: `client_chat` — Runtime Conversation Handler

**Purpose:** The brain behind each client's website chatbot.

**How it works:**
1. Public API receives a chat message → routes to this skill
2. **L1a runs inline**: lightweight critical signal detection (urgency, safety, escalation)
3. Loads tenant-specific config (system prompt, personality, allowed topics)
4. Generates response using the Agent with tenant context (adjusting if L1a flagged anything)
5. Returns response (or streams it via SSE)
6. **Fires async extraction pipeline**: L1b + L2 (on session close)

**Implementation:**
- New skill in `src/zetherion_ai/skills/client_chat.py`
- Wraps `Agent.generate_response_for_tenant()`
- Manages conversation context window per session
- Applies tenant-specific system prompts from `tenants.config`
- L1a detection as a fast pre-processing step (regex + lightweight classifier, not full LLM)

### Skill B3: `tenant_intelligence` — Entity Extraction FOR the Tenant

**Purpose:** Passively extracts information useful to the tenant (L1b + L2 "For Tenant" columns).

**Handles:**
- L1b (per-message, async): Contact entities, intent, sentiment, purchase signals
- L2 (per-session): Customer profile summary, conversation outcome, follow-up actions, topics

**Storage:** Tenant-scoped tables (tenant can access via API):
```sql
tenant_contacts (id, tenant_id, name, email, phone, source, tags[], custom_fields JSONB)
tenant_interactions (id, tenant_id, contact_id FK, session_id, type, summary, entities JSONB, sentiment)
```

**Implementation:**
- New skill in `src/zetherion_ai/skills/tenant_intelligence.py`
- Runs asynchronously after each `client_chat` response
- Uses LLM extraction (similar to Phase 9's personal understanding layer)
- Stores in tenant-scoped Postgres tables
- Data exposed back to tenant via API endpoints from the start

### Skill B4: `client_insights` — Relationship Intelligence FOR James

**Purpose:** Extracts signals useful to James (L1b-L5 "For James" columns).

**Handles:**
- L1b/L2 (per-message/session): Feeds sentiment + outcome into aggregation
- L3 (periodic): Volume trends, conversion rates, unmet needs, escalation rates per tenant
- L4 (periodic): Cross-tenant benchmarks, emerging patterns, churn risk
- L5 (continuous): Bot improvement suggestions, config recommendations

**How James consumes it:**
- **On-demand**: "How are my clients doing?" → summary from Qdrant + aggregation tables
- **Proactive alerts**: Threshold-based notifications ("Bob's satisfaction dropped 20%")

**Storage:**
- Per-conversation signals → James's Qdrant (tagged with `client_tenant_id`)
- Aggregated metrics → PostgreSQL aggregation table
- Alerts → notification dispatch

**Implementation:**
- New skill in `src/zetherion_ai/skills/client_insights.py`
- L1b/L2: Runs async after each response (parallel with B3, different extraction prompt)
- L3: Runs on daily/weekly heartbeat schedule
- L4: Runs on weekly heartbeat schedule
- L5: Triggered when L3/L4 metrics cross thresholds
- Stores in Qdrant with `owner_id=james` + `client_tenant_id` metadata

### Notification Dispatch (Cross-cutting)

**Purpose:** Route alerts/notifications to whatever platform James is using.

**Today:** Discord DM (existing `bot.get_user().send()` pattern)
**Tomorrow:** Slack webhook, email, push notification

**Implementation:**
- New module `src/zetherion_ai/notifications/dispatch.py`
- Simple interface: `await dispatch.notify(user_id, message, priority)`
- Backend registry: `{discord: DiscordNotifier, slack: SlackNotifier, ...}`
- Skills call `dispatch.notify()` instead of platform-specific code

---

## Key Existing Code to Reuse

| What | Where | Reuse for |
|------|-------|-----------|
| aiohttp server pattern | `src/zetherion_ai/skills/server.py` | Public API server |
| PostgreSQL manager pattern | `src/zetherion_ai/discord/user_manager.py` | TenantManager |
| Agent inference pipeline | `src/zetherion_ai/agent/core.py` | `generate_response_for_tenant()` |
| Qdrant user scoping | `src/zetherion_ai/memory/qdrant.py` | Scope by `tenant_id` |
| Skill registration | `src/zetherion_ai/skills/registry.py` | Register all new skills |
| Personal understanding (Phase 9) | `src/zetherion_ai/personal/` | Pattern for entity extraction in B3/B4 |
| Pydantic settings | `src/zetherion_ai/config.py` | New config fields |
| TestServer pattern | `tests/integration/test_skills_http.py` | API integration tests |
| Docker multi-stage | `Dockerfile.skills` | `Dockerfile.api` |

## Implementation Order

Recommended sequence (workstreams can partially overlap):

1. **A1** (Foundation) — Must come first, establishes tenant infrastructure
2. **B1** (Provisioning skill) — Can start as soon as A1's TenantManager exists
3. **A2** (Chat endpoint) — Depends on A1
4. **B2** (Chat skill) — Developed alongside A2
5. **A3** (SSE streaming) — Depends on A2
6. **B3** (Tenant intelligence) — Can start after B2 is working
7. **B4** (Client insights) — Can start after B2 is working, parallel with B3
8. **A4** (Cloudflare + hardening) — Final production step
9. **Notification dispatch** — Can be built incrementally alongside B4

## Verification

After each phase:
1. `ruff check src/ tests/` passes
2. `pytest tests/ -m "not integration and not discord_e2e"` passes
3. In-process integration tests pass
4. Existing Discord bot tests remain green

End-to-end (after A4):
1. All services healthy including `zetherion-api` and `cloudflared`
2. Health check returns 200 via public URL
3. Create tenant → get API key → create session → chat → get streaming AI response
4. Tenant intelligence extracts entities into tenant-scoped tables
5. Client insights surface trends to James via Discord
6. Discord bot continues working normally

## Dependencies to Add

- `PyJWT` — Session token signing/verification
- `bcrypt` — API key hashing
