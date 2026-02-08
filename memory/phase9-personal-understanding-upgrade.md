# Phase 9: Personal Understanding Upgrade

**Status**: Queued (Deferred until prior recommendations are implemented)
**Dependencies**: Prior codebase hardening recommendations, Phase 8 Gmail Correlator
**Created**: 2026-02-08

---

## Overview

Design and wire a personal understanding layer that learns who you are, then uses that model to run a multi-account Gmail and calendar command center with daily briefings, suggested actions, and approval-aware automation.

---

## Upgrade Scope

1. Personal Model
- Identity, preferences, communication style, goals, working patterns.
- Relationship graph (contacts, importance, norms).
- Action policy profile (what can be auto-drafted, auto-sent, auto-RSVPed).

2. Memory and Inference Wiring
- Route message/email/calendar events into structured user memory.
- Use confidence scoring, confirmation queues, decay, and correction handling.
- Ensure strict `user_id` scoping on read/write paths.

3. Decision Context Layer
- Build a compact "decision pack" before summaries/replies:
  - relevant profile facts
  - relationship context
  - schedule constraints
  - policy gates

4. Inbox + Calendar Operations
- Aggregate all Google inboxes into one operational queue.
- Classify, prioritize, summarize, and propose actions.
- Manage meeting invites and calendar conflicts with policy-aware recommendations.

5. Action Controls
- Modes: read-only, draft-only, approved-send, limited-autopilot.
- Full audit trail for what was done and why.
- Fast override commands ("never do this", "ask me first", "forget this").

---

## Proposed Skills

- `gmail_connector_skill`
- `gmail_sync_skill`
- `calendar_sync_skill`
- `daily_briefing_skill`
- `assistant_actions_skill`
- `personal_model_skill`

---

## Rollout Plan

1. Read-only aggregation and daily briefings.
2. Draft-only email replies and RSVP suggestions.
3. Approval-gated sends/RSVP actions.
4. Limited autopilot for explicitly allowed scenarios.

---

## Start Condition

Begin Phase 9 only after prior recommendations are complete, especially:
- memory isolation and user scoping fixes
- encryption wiring at runtime
- skill registration/bootstrapping and prompt-fragment integration
