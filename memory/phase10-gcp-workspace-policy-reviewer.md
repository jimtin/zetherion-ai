# Phase 10: GCP + Workspace Policy Reviewer Skill

**Status**: Planning
**Dependencies**: Phase 5D (Skills Framework), Phase 5A (Encryption), Phase 9 (Personal Understanding Upgrade)
**Created**: 2026-02-08

---

## Overview

Comprehensive security posture review skill for Google Cloud Platform and Google Workspace.  
The skill performs read-only policy and configuration analysis against security best practices, then produces prioritized recommendations using a configurable CIA weighting model (Availability, Confidentiality, Integrity).

---

## Goals

- Scan GCP posture across organization, folder, and project scope.
- Scan Google Workspace posture for admin, identity, sharing, and app risk controls.
- Detect policy drift and high-risk misconfigurations with deterministic checks.
- Prioritize findings using user-defined CIA weighting.
- Produce actionable, auditable recommendations with effort and blast-radius estimates.

---

## Non-Goals (Phase 10)

- No auto-remediation by default.
- No write operations to cloud/workspace settings.
- No vulnerability scanning of application code.
- No agentic “fix everything” mode without explicit user approval.

---

## Architecture

```
┌──────────────────────────────┐     ┌──────────────────────────────┐
│      Google Cloud APIs       │     │    Google Workspace APIs     │
│ Asset, IAM, Org Policy, SCC  │     │ Admin SDK, Reports, Alerts   │
└───────────────┬──────────────┘     └───────────────┬──────────────┘
                │                                      │
                └──────────────┬───────────────────────┘
                               ▼
                   ┌────────────────────────┐
                   │   Evidence Collector   │
                   │   (read-only ingest)   │
                   └────────────┬───────────┘
                                ▼
                   ┌────────────────────────┐
                   │ Deterministic Rule     │
                   │ Engine + Control Packs │
                   └────────────┬───────────┘
                                ▼
                   ┌────────────────────────┐
                   │ CIA Risk Prioritizer   │
                   │ (A/C/I weighted score) │
                   └────────────┬───────────┘
                                ▼
                   ┌────────────────────────┐
                   │ Recommendation Planner │
                   │ + Report Generator     │
                   └────────────────────────┘
```

---

## Primary Capabilities

1. Multi-scope Cloud Posture Review
- IAM bindings and privilege escalation paths.
- Organization Policy conformance.
- Logging, monitoring, and alerting coverage.
- Network and perimeter guardrails.
- Key management and secret controls.
- Storage/database/public exposure checks.

2. Workspace Posture Review
- Super admin hardening.
- MFA and account recovery posture.
- External sharing and DLP-aligned controls.
- OAuth app governance and risky app grants.
- Group and domain-level trust settings.
- Alert Center signal review and risky event trends.

3. CIA-Weighted Prioritization
- User sets weights for Availability, Confidentiality, Integrity.
- Same findings are re-ranked based on business risk posture.
- Output includes “why this ranks high for your weighting.”

4. Recommendation Intelligence
- “Fix now”, “fix this sprint”, and “plan later” buckets.
- Effort and blast-radius estimates.
- Suggested guardrail policy patterns and rollout order.

5. Drift and Scheduled Reviews
- Baseline snapshot and delta reporting.
- Weekly or monthly policy drift review mode.

---

## Data Sources and APIs

### GCP APIs
- Cloud Asset Inventory API
- Cloud Resource Manager API
- IAM API
- Organization Policy API
- Security Command Center API
- Cloud Logging API
- Cloud Monitoring API
- Service Usage API

### Workspace APIs
- Admin SDK Directory API
- Admin SDK Reports API
- Alert Center API
- Cloud Identity APIs (where applicable)

### Access Model
- Read-only service account roles for cloud posture collection.
- Domain-wide delegation only for required Workspace read scopes.
- Token and secret material encrypted at rest.
- Full query/audit logs for every review run.

---

## Control Packs

- CIS Google Cloud Foundations Benchmark (versioned profile).
- Google Cloud Security Foundations controls.
- Workspace hardening baseline profile.
- Optional mappings to NIST CSF / ISO 27001 tags for reporting.

Each control must include:
- control ID
- deterministic check logic
- rationale
- false-positive notes
- remediation guidance

---

## CIA Scoring Model

Each finding is assigned:
- `severity` (Critical/High/Medium/Low)
- `likelihood` (0.0-1.0)
- `asset_criticality` (0.0-1.0)
- `impactA`, `impactC`, `impactI` (0.0-1.0)

User-configurable weighting:
- `wA + wC + wI = 1.0`

Risk score:

`risk = severity_factor * likelihood * asset_criticality * (wA*impactA + wC*impactC + wI*impactI) * 100`

Severity factors:
- Critical: 1.0
- High: 0.8
- Medium: 0.5
- Low: 0.2

---

## Recommendation Format

Each finding includes:
- What is misconfigured.
- Why it matters.
- CIA impact profile.
- User-weighted priority score.
- Recommended action.
- Alternative lower-risk rollout approach.
- Estimated effort (S/M/L) and blast radius (Low/Medium/High).
- Verification steps after remediation.

---

## Output Artifacts

1. Executive Summary (Markdown)
- top risks
- CIA-weighted priorities
- quick wins
- deferred items

2. Technical Findings (JSON + Markdown)
- full finding inventory
- evidence references
- control mapping

3. Remediation Plan
- ordered backlog by priority and effort
- implementation sequence to reduce operational risk

4. Drift Delta Report (for recurring runs)
- new findings
- resolved findings
- score trend movement

---

## Implementation Phases

### Phase 10A: Foundation and Read-Only Connectors

- Build auth and evidence collectors for GCP + Workspace.
- Establish encrypted credential handling.
- Define normalized evidence schema.

### Phase 10B: Deterministic Policy Rule Engine

- Implement versioned control checks.
- Add baseline GCP + Workspace control packs.
- Create finding schema and evidence linking.

### Phase 10C: CIA Prioritizer

- Implement weighted scoring and ranking.
- Add profile presets:
  - Confidentiality-first
  - Integrity-first
  - Availability-first
  - Balanced

### Phase 10D: Recommendation Planner and Reporting

- Generate executive and technical reports.
- Add remediation sequencing and verification guidance.
- Emit machine-readable JSON for automation/ticketing.

### Phase 10E: Drift Detection and Scheduling

- Add baseline snapshots and differential analysis.
- Add recurring review mode and trend scoring.

### Phase 10F: Optional Integrations

- Create GitHub/Jira issues from selected findings.
- Post summary alerts to Discord/Slack.

---

## Security and Safety Controls

- Read-only mode default and enforced.
- Scope-limited credentials and least privilege.
- No plaintext secrets in logs.
- Explicit handling for unsupported APIs/permissions.
- Deterministic findings required before recommendation generation.
- LLM usage limited to explanation and formatting, not detection.

---

## Event Bus Integration

| Event | Consumers |
|-------|-----------|
| `policy_review_completed` | Reporting, notifications |
| `critical_finding_detected` | Discord DM alert, incident channel |
| `drift_detected` | Weekly digest, security ops |
| `remediation_backlog_created` | GitHub/Jira sync skill |

---

## Skill Metadata (Draft)

```yaml
name: gcp-workspace-policy-reviewer
description: Review Google Cloud and Google Workspace security posture against best practices and generate CIA-weighted recommendations. Use when assessing cloud/workspace policy risk, preparing remediation plans, or running recurring posture reviews.
version: 1.0.0
```

---

## Initial User Commands (Examples)

- "Run a balanced policy review for org `acme-prod`."
- "Review project `my-app-prod` with confidentiality-first weighting."
- "Check Workspace posture and show only high-risk integrity findings."
- "Compare this week vs last week drift and prioritize quick wins."
