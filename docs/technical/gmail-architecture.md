# Gmail Architecture

## Overview
The Gmail integration is a 12-file module (src/zetherion_ai/skills/gmail/) providing email management with a progressive trust system for reply automation.

## Module Structure
| File | Purpose |
|------|---------|
| skill.py | Entry point, intent routing (7 intents) |
| trust.py | Per-contact and per-type trust scoring |
| accounts.py | Multi-account management with encrypted tokens |
| inbox.py | Unified inbox aggregation |
| digest.py | Morning/evening/weekly digest generation |
| replies.py | AI-powered reply generation and classification |
| analytics.py | Email analytics and relationship scoring |
| auth.py | OAuth2 authentication |
| client.py | Gmail API client |
| sync.py | Email synchronization |
| calendar_sync.py | Calendar integration |
| conflicts.py | Calendar conflict detection |

## Trust System

### Two-Dimensional Trust
Trust is tracked along two dimensions:
1. **Type Trust**: Per reply type (e.g., how much to trust acknowledgment replies globally)
2. **Contact Trust**: Per sender (e.g., how much to trust replies to your manager)

### Effective Trust Calculation
```
effective_trust = min(type_trust, contact_trust, reply_type_ceiling)
```

### Trust Evolution
| Event | Delta |
|-------|-------|
| User approves draft | +0.05 |
| User makes minor edit | -0.02 |
| User makes major edit | -0.10 |
| User rejects draft | -0.20 |
| Floor | 0.00 |
| Global cap | 0.95 |

### Reply Type Ceilings
| Reply Type | Ceiling | Rationale |
|------------|---------|-----------|
| ACKNOWLEDGMENT | 0.95 | Low risk, formulaic |
| MEETING_CONFIRM | 0.90 | Simple confirmation |
| MEETING_DECLINE | 0.80 | Needs care with wording |
| INFO_REQUEST | 0.75 | Content-dependent |
| TASK_UPDATE | 0.70 | Context-dependent |
| GENERAL | 0.60 | Variable risk |
| NEGOTIATION | 0.50 | High stakes |
| SENSITIVE | 0.30 | Highest risk |

### Auto-Send Decision
A reply is auto-sent when BOTH conditions are met:
- effective_trust >= 0.85
- confidence >= 0.85

### Database Schema
```sql
gmail_type_trust (user_id, reply_type, score, approvals, rejections, edits, total_interactions)
gmail_contact_trust (user_id, contact_email, score, approvals, rejections, edits, total_interactions)
```

## Reply Pipeline
1. Email received and classified by ReplyClassifier (keyword matching)
2. Reply type determined (8 types from ACKNOWLEDGMENT to SENSITIVE)
3. ReplyGenerator creates draft using InferenceBroker with type-specific templates
4. Confidence score calculated: (ceiling * 0.7) + bonuses - penalties
5. Trust check: if effective_trust >= 0.85 AND confidence >= 0.85, auto-send
6. Otherwise, draft stored as PENDING for user review
7. User approves/edits/rejects, trust evolves accordingly

## Account Management
- Multi-account support (first account = primary)
- OAuth tokens encrypted with AES-256-GCM before storage
- Token refresh handled automatically
- Sync state tracked per account (history_id, last sync timestamps)

### Database Schema
```sql
gmail_accounts (id, user_id, email, access_token_encrypted, refresh_token_encrypted, token_expiry, scopes, is_primary, last_sync)
gmail_sync_state (account_id, history_id, last_full_sync, last_partial_sync)
gmail_drafts (id, email_id, account_id, draft_text, reply_type, confidence, status, sent_at)
```

## Digest Generation
Three digest types:
- **Morning**: Unread summary by classification, today's volume, pending drafts
- **Evening**: Day summary, drafted count, neglected threads (>2 days)
- **Weekly**: 7-day volume trends, top 5 contacts, classification breakdown, neglected threads (>5 days)

## Analytics
- Contact relationship scoring: volume (log scale, 0.4 max) + recency (0.3 max) + response time (0.2 max)
- Period statistics: received/sent/drafted counts, average response time
- Top senders identification
- Neglected thread detection

## Heartbeat Integration
The Gmail skill provides heartbeat actions for periodic digest generation. When users have connected accounts, the scheduler triggers email_digest at configured intervals.

## Related Docs
Links to: architecture.md, security.md (OAuth, trust), configuration.md (Gmail vars)
