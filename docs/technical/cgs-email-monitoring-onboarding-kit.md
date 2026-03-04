# CGS Email Monitoring Onboarding Kit (Google-First)

## Audience

This kit is for CGS implementation teams and external operator teams onboarding tenant-scoped multi-mailbox monitoring through CGS only.

Authoritative exposure rule:
- Public/admin clients integrate only with CGS `/service/ai/v1/...`.
- Zetherion email admin APIs are internal-only and never exposed directly.

## Maintenance Note (2026-03-04)

- Zetherion-only boundary recovery removed in-repo CGS website/UI assets.
- Email monitoring API contracts in this onboarding kit are unchanged.
- Internal trust-policy gating now enforces deny-by-default for sensitive namespaces and approval flow reuse for high-risk actions.

## Capability Outcome

After implementing this kit, operators can:
- Configure per-tenant Google OAuth app credentials securely.
- Connect and manage 5+ mailboxes per tenant.
- Trigger mailbox sync jobs (email + calendar context).
- Triage critical messages with reason codes and confidence.
- Query normalized insights for downstream skill extraction.
- Set mailbox primary calendar for controlled write flows.

## Authoritative Endpoint Surface (CGS)

Prefix: `/service/ai/v1/internal/admin/tenants/{tenant_id}/email`

- `GET /providers/google/oauth-app`
- `PUT /providers/google/oauth-app`
- `POST /mailboxes/connect/start`
- `GET /mailboxes/connect/callback`
- `GET /mailboxes`
- `PATCH /mailboxes/{mailbox_id}`
- `DELETE /mailboxes/{mailbox_id}`
- `POST /mailboxes/{mailbox_id}/sync`
- `GET /critical/messages`
- `GET /calendars`
- `PUT /mailboxes/{mailbox_id}/calendar-primary`
- `GET /insights`
- `POST /insights/reindex`

Contract source:
- `docs/technical/openapi-cgs-gateway.yaml`

## Auth, Scope, and Approval Rules

Required headers:
- `Authorization: Bearer <cgs_jwt>`
- `Idempotency-Key: <key>` for retry-prone mutations

Required scopes:
- All routes: operator + `cgs:zetherion-admin`
- OAuth app routes: additionally `cgs:zetherion-secrets-admin`

Step-up requirement:
- All mutating routes require step-up claim (`step_up=true` or accepted MFA `acr/amr` claim).

Approval workflow requirements:
- `PUT /providers/google/oauth-app` requires approved `change_ticket_id`.
- `DELETE /mailboxes/{mailbox_id}` requires approved `change_ticket_id`.
- Two-person approval is enforced.

## Core Data Contracts

Mailbox status:
- `pending|connected|degraded|revoked|disconnected`

Sync job status:
- `queued|running|succeeded|failed|retrying`

Critical item severity/state:
- severity: `critical|high|normal`
- status: `open|resolved|dismissed`

## Operator Flows

### Flow A: Configure OAuth App

1. Read current config (`GET /providers/google/oauth-app`).
2. Submit approved change ticket via existing admin `/changes` route family.
3. Save config (`PUT /providers/google/oauth-app`) with `redirect_uri`, optional secret refs/values, and `change_ticket_id`.
4. Confirm read response shows metadata + `has_client_id`/`has_client_secret` without revealing secret values.

### Flow B: Connect Mailboxes (5+)

1. Start OAuth connect (`POST /mailboxes/connect/start`).
2. Redirect operator to returned provider authorization URL.
3. Complete browser callback (`GET /mailboxes/connect/callback?code=...&state=...`).
4. Refresh mailbox list (`GET /mailboxes`) and verify all targeted mailboxes appear as `connected`.

### Flow C: Initial Sync + Triage

1. Trigger sync per mailbox (`POST /mailboxes/{mailbox_id}/sync`), default direction `bi_directional`.
2. Pull critical inbox (`GET /critical/messages`) with filters (`status`, `severity`, `limit`).
3. Pull insights (`GET /insights`) with filters (`insight_type`, `min_confidence`, `limit`).
4. Optional recovery: `POST /insights/reindex` to rebuild vector-linked insight records.

### Flow D: Calendar Controls

1. Load calendars for mailbox (`GET /calendars?mailbox_id=...`).
2. Set primary calendar (`PUT /mailboxes/{mailbox_id}/calendar-primary`).
3. Keep delete operations disabled for calendar writes unless explicitly enabled by policy in a later phase.

## Security and Compliance Expectations

- OAuth and mailbox credentials are encrypted at rest in tenant-scoped stores.
- OAuth app read paths never return plaintext secret values.
- All mutations are auditable with actor, request ID, and change ticket linkage.
- Replay-resistant actor envelopes are enforced in upstream internal calls.
- Retention defaults:
  - Message bodies/content: 90 days
  - Metadata/critical/insights: 365 days

## UI Wiring Expectations

Operator UI should provide:
- OAuth app setup screen (masked secrets, no readback).
- Mailbox onboarding wizard (start + callback + status).
- Critical inbox screen (severity/confidence/reason codes).
- Calendar sync screen (mailbox calendars + primary selection).
- Insights screen (filterable structured extraction records).

Route-to-screen mapping source:
- `docs/technical/frontend-route-wiring.md`

## UAT Checklist

1. Configure tenant OAuth app with approval gate and secrets scope.
2. Connect at least 5 Google mailboxes successfully.
3. Verify token refresh path by forcing one near-expiry mailbox and re-syncing.
4. Confirm critical items are created with severity, score, and reason codes.
5. Confirm calendar list and primary calendar set flows work.
6. Confirm cross-tenant access is denied.
7. Confirm mailbox disconnect requires approved `change_ticket_id`.
8. Confirm secret values are never returned by read endpoints.

## Go-Live Checklist

1. Operator JWTs include required scopes and step-up claims.
2. Approval workflow is enabled and staffed for two-person review.
3. CGS error handling maps `AI_AUTH_*`, `AI_APPROVAL_*`, and upstream errors correctly.
4. Observability dashboards include mailbox sync failures and critical backlog.
5. Runbook includes OAuth callback failures, token refresh failures, and sync retry handling.

## Supporting Docs

- `docs/technical/cgs-public-api-endpoint-build-spec.md`
- `docs/technical/api-auth-matrix.md`
- `docs/technical/api-error-matrix.md`
- `docs/technical/openapi-cgs-gateway.yaml`
- `docs/technical/frontend-route-wiring.md`
