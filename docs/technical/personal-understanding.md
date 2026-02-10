# Personal Understanding

## Overview
The Personal Understanding layer builds a comprehensive model of each user using PostgreSQL storage. It maintains profiles, a contact graph, action policies, and accumulated learnings.

## Data Model

### PersonalProfile
Core user identity and preferences:
| Field | Type | Description |
|-------|------|-------------|
| user_id | int | Discord user ID (PK) |
| display_name | str | User's preferred name |
| timezone | str | Timezone (e.g., "AEDT") |
| locale | str | Language/locale |
| working_hours | JSON | {start, end, days} |
| communication_style | JSON | {formality, verbosity, emoji_usage, humor} 0-1 scales |
| goals | JSON[] | List of user goals |
| preferences | JSON | Free-form key/value preferences |

### PersonalContact (Contact Graph)
Tracked relationships:
| Field | Type | Description |
|-------|------|-------------|
| user_id | int | Owner |
| contact_email | str | Contact identifier |
| contact_name | str | Display name |
| relationship | enum | COLLEAGUE, CLIENT, FRIEND, MANAGER, VENDOR, FAMILY, ACQUAINTANCE, OTHER |
| importance | float | 0-1 importance score |
| company | str | Organization |
| interaction_count | int | Total interactions |
| last_interaction | datetime | Most recent |

### PersonalPolicy
Per-domain action permissions:
| Field | Type | Description |
|-------|------|-------------|
| user_id | int | Owner |
| domain | enum | EMAIL, CALENDAR, TASKS, GENERAL, DISCORD_OBSERVE |
| action | str | Specific action |
| mode | enum | AUTO, DRAFT, ASK, NEVER |
| conditions | JSON | Optional approval conditions |
| trust_score | float | Learned trust (0-1) |

Policy Modes:
- AUTO: Execute immediately
- DRAFT: Create draft for review
- ASK: Request explicit approval
- NEVER: Block the action entirely

### PersonalLearning
Accumulated observations:
| Field | Type | Description |
|-------|------|-------------|
| user_id | int | Owner |
| category | enum | PREFERENCE, CONTACT, SCHEDULE, POLICY, CORRECTION, FACT |
| content | str | What was learned |
| confidence | float | 0-1 confidence |
| source | enum | EXPLICIT, INFERRED, EMAIL, CALENDAR, DISCORD |
| confirmed | bool | User-confirmed |

## PostgreSQL Schema
```sql
personal_profile (user_id PK, display_name, timezone, locale, working_hours JSONB, communication_style JSONB, goals JSONB, preferences JSONB, updated_at)

personal_contacts (id PK, user_id, contact_email, contact_name, relationship, importance, company, notes, last_interaction, interaction_count, updated_at)
UNIQUE (user_id, contact_email)

personal_policies (id PK, user_id, domain, action, mode, conditions JSONB, trust_score, created_at, updated_at)
UNIQUE (user_id, domain, action)

personal_learnings (id PK, user_id, category, content, confidence, source, confirmed, created_at)
```

## Storage Layer (PersonalStorage)
CRUD operations via asyncpg connection pool:
- Profile: upsert_profile, get_profile, delete_profile
- Contacts: upsert_contact, list_contacts, increment_contact_interaction
- Policies: upsert_policy, list_policies, update_trust_score, reset_domain_trust
- Learnings: add_learning, list_learnings, confirm_learning, delete_learning

## Communication Style
Four dimensions (0.0 to 1.0):
| Dimension | Low (0.0) | High (1.0) |
|-----------|-----------|------------|
| Formality | Casual | Formal |
| Verbosity | Terse | Detailed |
| Emoji Usage | Never | Frequent |
| Humor | Serious | Playful |

Defaults configurable via DEFAULT_FORMALITY, DEFAULT_VERBOSITY.

## Contact Graph
- Implicit graph via personal_contacts table
- Relationships typed (8 categories)
- Importance scored 0-1
- Interaction tracking (count + recency)
- Ordered by importance DESC, interaction_count DESC
- Used for: Gmail trust context, response personalization

## Policy System
- Per-domain policies control bot autonomy
- Trust scores learned from user feedback over time
- Domains: EMAIL, CALENDAR, TASKS, GENERAL, DISCORD_OBSERVE
- Supports conditional approval (JSON conditions field)
- Trust can be reset per domain

## Integration with Gmail
- Contact graph updated from email interactions
- Gmail trust scores separate but complementary
- Email policies control reply automation
- Calendar policies control scheduling

## Related Docs
Links to: observation-pipeline.md, gmail-architecture.md, architecture.md, configuration.md
