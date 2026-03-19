# Provider-Agnostic Work Router Plan (Email + Tasks + Calendar)

## Summary

Implement a shared routing core that ingests email/task/calendar content from
multiple providers, blocks malicious content first, triages with the small
local model, extracts with the larger local model, and routes into task
lists/calendars with conflict-aware policies.

This keeps Google working now, adds Outlook-ready interfaces, and preserves
backward compatibility with existing Gmail intents.

## Success Criteria

1. Malicious email content is explicitly rejected before extraction or writes.
2. Discord malicious content continues to be explicitly rejected (existing behavior
   preserved).
3. Small model is used only for security + triage, never final extraction.
4. Larger local model is the default extractor for email/task/calendar.
5. Cloud fallback happens only when larger local extraction fails/timeouts.
6. Multiple calendars are supported with primary writable calendar selection.
7. Conflict checks run across all connected calendars, not just primary.
8. Router interfaces are provider-agnostic and support Google now, Outlook later.
9. OAuth credentials can be updated at runtime without service restart.
10. Mailboxes can be added/removed dynamically and reflected immediately.
11. Unread ingestion scans all connected Google mailboxes (primary first), not a
    single account only.

## Locked Architecture Decisions

1. Security gate is first and mandatory for all ingestion sources (`email`,
   `task`, `calendar`).
2. Security `BLOCK` is terminal: no extraction, no routing writes.
3. Security `FLAG` is non-terminal for storage but routes to review queue, not
   auto-write.
4. Small local model handles only route tags (`task_candidate`,
   `calendar_candidate`, `reply_candidate`, `digest_only`, `ignore`).
5. Larger local model performs structured extraction/classification.
6. Fallback chain on local extraction failure: `Gemini -> Claude -> OpenAI`
   (only if configured/available).
7. Primary destination is per-provider scope; conflict visibility is global
   across connected calendars.
8. Two-way sync is enabled for routed objects using external/local object link
   mapping.

## Public API / Interface / Type Changes

| Area | Change | File |
|---|---|---|
| Skills | Add provider-agnostic email skill entrypoint `email` | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/skills/email.py` |
| Skills | Keep Gmail compatibility wrapper/alias | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/skills/gmail/skill.py` |
| Agent routing | Route `EMAIL_MANAGEMENT` to `email` skill (not hardcoded `gmail`) | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/agent/core.py` |
| OAuth routes | Add `GET /oauth/{provider}/authorize` and `GET /oauth/{provider}/callback`; keep `/gmail/callback` alias | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/skills/server.py` |
| Runtime credentials | Add secrets APIs (`GET/PUT/DELETE /secrets`) and dynamic OAuth resolver | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/skills/server.py` |
| Router contracts | Add `IngestionEnvelope`, `RouteTag`, `RouteDecision`, `ConflictDecision`, normalized item types | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/routing/models.py` |
| Provider contracts | Add `EmailProviderAdapter`, `TaskProviderAdapter`, `CalendarProviderAdapter` protocols | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/providers/base.py` |
| Router services | Add `TaskCalendarRouter`, `EmailRouter`, `ProviderRegistry` | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/routing/task_calendar_router.py`, `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/routing/email_router.py`, `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/routing/registry.py` |
| Security core | Extract reusable content-security pipeline from Discord-specific package | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/security/content_pipeline.py` |
| Email operations | Add connect/disconnect intents for runtime mailbox management | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/skills/email.py` |

## Data Model and Storage Changes

| Table | Purpose | Storage File |
|---|---|---|
| `integration_accounts` | Connected provider accounts | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `integration_destinations` | Calendars/task-lists/mailboxes + primary flags | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `integration_sync_state` | Incremental sync cursors/tokens metadata | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `integration_email_messages` | Canonical inbound message records | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `integration_object_links` | Local↔provider object mapping for two-way sync | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `routing_preferences` | User policy (auto/ask/draft/never, primary destination choices) | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `routing_decisions` | Audit trail of triage/extraction/route decisions | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |
| `integration_security_events` | Blocked/flagged content events and reasons | `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/storage.py` |

## Routing and Conflict Policy (Exact Defaults)

1. Security thresholds reuse current defaults: `flag >= 0.3`, `block >= 0.6`.
2. On `BLOCK`: store security event metadata and stop processing.
3. On `FLAG`: store item, send to review queue, do not auto-create task/event.
4. Conflict detection reads all connected calendars in scope window.
5. Conflict severity policy: `>=0.6` always ask; `0.25-0.59` ask when priority
   high or attendee-impacting, else draft; `<0.25` auto.
6. Unscheduled actionable items route to primary task list.
7. Scheduled actionable items route to primary calendar after conflict pass.
8. If no primary destination is set, ask user to choose once and persist.

## Provider Plan

1. Implement Google adapters fully for Gmail, Calendar, and Google Tasks in
   `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/providers/google.py`.
2. Add Outlook adapter scaffold (disabled by flag) in
   `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/integrations/providers/outlook.py`.
3. Add provider capability registry (read/write/sync/conflict support) in
   `/Users/jameshinton/Documents/Developer/PersonalBot/src/zetherion_ai/routing/registry.py`.

## Implementation Phases

1. Phase 1: Foundation and wiring.
   Deliverables: new shared security pipeline, new router model contracts,
   skills server OAuth callback route, env/config completion.
2. Phase 2: Canonical integration storage.
   Deliverables: integration tables, data access layer, migration readers for
   legacy `gmail_*`.
3. Phase 3: Provider adapters.
   Deliverables: Google full adapter, Outlook scaffold and feature flag.
4. Phase 4: Task and calendar router.
   Deliverables: normalized routing pipeline, cross-calendar conflict resolution,
   primary destination policy.
5. Phase 5: Email router and ingestion classification.
   Deliverables: provider-neutral email skill, triage tags, extraction
   orchestration, malicious rejection path.
6. Phase 6: Observation pipeline integration.
   Deliverables: email-derived items feed shared router, not Gmail-specific
   direct path.
7. Phase 7: Agent routing migration and compatibility.
   Deliverables: `EMAIL_MANAGEMENT -> email` mapping, keep legacy Gmail intent
   compatibility.
8. Phase 8: Rollout and observability.
   Deliverables: feature flags, metrics, review queue ops, rollback safety.
9. Phase 9: Runtime integration ops + local validation.
   Deliverables: runtime credential set/rotate without restart, dynamic mailbox
   add/remove, validated local runbook and operator checks.

## Test Cases and Scenarios

1. Malicious email with prompt injection markers returns `BLOCK` and no
   downstream writes.
2. Benign email task request is extracted by large local model and written to
   primary task list.
3. Scheduled task from email triggers conflict scan against all calendars.
4. Major conflict (`>=0.6`) prompts user confirmation before write.
5. Local extraction timeout triggers cloud fallback and succeeds.
6. Local extraction success does not invoke cloud fallback.
7. `FLAG` verdict stores review item and performs no auto-write.
8. Duplicate message across two sources deduplicates via external link key.
9. Two-way sync external update maps to existing local object and updates in
   place.
10. No primary calendar configured triggers one-time primary selection prompt.
11. Legacy Gmail commands still respond via compatibility alias.
12. Discord malicious message remains blocked before agent response path.
13. Runtime credential updates are honored immediately by OAuth authorization
   and callback paths.
14. Multiple mailboxes can be linked in a single running session.
15. Unread ingestion includes all linked mailbox sources.
16. `email_disconnect` removes one mailbox and monitoring for that address stops
   immediately.
17. Credential rotation succeeds without process restart.

## Runtime Local Validation (No Restart)

```bash
export SKILLS_URL="https://localhost:8080"
export SKILLS_API_SECRET="replace-if-enabled"
export USER_ID="123456789"
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export GOOGLE_REDIRECT_URI="https://localhost:8080/gmail/callback"
```

Set runtime values:

```bash
curl -sS -X PUT "$SKILLS_URL/settings/integrations/google_client_id" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"value\":\"$GOOGLE_CLIENT_ID\",\"changed_by\":$USER_ID,\"data_type\":\"string\"}"
```

```bash
curl -sS -X PUT "$SKILLS_URL/settings/integrations/google_redirect_uri" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"value\":\"$GOOGLE_REDIRECT_URI\",\"changed_by\":$USER_ID,\"data_type\":\"string\"}"
```

```bash
curl -sS -X PUT "$SKILLS_URL/secrets/google_client_secret" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"value\":\"$GOOGLE_CLIENT_SECRET\",\"changed_by\":$USER_ID,\"description\":\"Google OAuth client secret\"}"
```

Generate auth URL:

```bash
curl -sS "$SKILLS_URL/oauth/google/authorize?user_id=$USER_ID" \
  -H "X-API-Secret: $SKILLS_API_SECRET"
```

Check status:

```bash
curl -sS -X POST "$SKILLS_URL/handle" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"user_id\":\"$USER_ID\",\"intent\":\"email_status\",\"message\":\"email status\",\"context\":{\"skill_name\":\"email\",\"provider\":\"google\"}}"
```

Route unread:

```bash
curl -sS -X POST "$SKILLS_URL/handle" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"user_id\":\"$USER_ID\",\"intent\":\"email_route\",\"message\":\"route unread email\",\"context\":{\"skill_name\":\"email\",\"provider\":\"google\",\"limit\":20}}"
```

Disconnect one mailbox:

```bash
export ACCOUNT_EMAIL_TO_REMOVE="you@example.com"

curl -sS -X POST "$SKILLS_URL/handle" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"user_id\":\"$USER_ID\",\"intent\":\"email_disconnect\",\"message\":\"disconnect $ACCOUNT_EMAIL_TO_REMOVE\",\"context\":{\"skill_name\":\"email\",\"provider\":\"google\",\"account_email\":\"$ACCOUNT_EMAIL_TO_REMOVE\"}}"
```

## Rollout Plan

1. Add feature flags with defaults: `work_router_enabled=false`,
   `provider_outlook_enabled=false`, `email_security_gate_enabled=true`,
   `local_extraction_required=true`.
2. Enable for owner account first, then expand by allowlist.
3. Track metrics: security block rate, local extraction failure rate, cloud
   fallback rate, conflict prompt rate, auto-route success rate.
4. Cut over default routing only after block/false-positive/fallback metrics
   stabilize for 7 days.

## Assumptions and Defaults

1. User timezone from profile is authoritative; fallback UTC.
2. Primary destination is scoped per provider.
3. If no writable destination exists, route to review queue with explicit action
   request.
4. Security bypass remains disabled by default.
5. Sensitive blocked payloads are not stored in full text by default; only
   hashed/reference metadata plus reason.
6. Existing Discord security behavior is preserved unchanged while the shared
   security pipeline is introduced for non-Discord ingress.
