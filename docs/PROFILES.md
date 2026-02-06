# Profile System Guide

The Profile System enables Zetherion AI to learn and remember user preferences, adapting responses to individual needs while respecting privacy.

## Table of Contents

- [Overview](#overview)
- [Configuration](#configuration)
- [Profile Categories](#profile-categories)
- [Automatic Learning](#automatic-learning)
- [Manual Profile Management](#manual-profile-management)
- [Confidence and Confirmation](#confidence-and-confirmation)
- [Response Adaptation](#response-adaptation)
- [Privacy and Data Control](#privacy-and-data-control)
- [Troubleshooting](#troubleshooting)

## Overview

The Profile System:

- **Learns automatically** from conversations
- **Tracks confidence** in learned information
- **Asks for confirmation** when uncertain
- **Adapts responses** to preferences
- **Respects privacy** with full user control

### How It Works

```
Conversation → Profile Inference → Confidence Scoring → Storage
                                          ↓
                              Confirmation (if uncertain)
                                          ↓
                              Response Adaptation
```

## Configuration

### Enable Profile System

```env
# Enable profile inference
PROFILE_INFERENCE_ENABLED=true

# Confidence threshold for auto-apply (0.0-1.0)
PROFILE_CONFIDENCE_THRESHOLD=0.6

# Cache TTL in seconds
PROFILE_CACHE_TTL=300

# Database location
PROFILE_DB_PATH=data/profiles.db
```

### Confirmation Settings

```env
# Maximum pending confirmations per user
PROFILE_MAX_PENDING_CONFIRMATIONS=5

# Hours before confirmation expires
PROFILE_CONFIRMATION_EXPIRY_HOURS=24
```

### Response Defaults

```env
# Default communication style (0.0-1.0)
DEFAULT_FORMALITY=0.5    # 0=casual, 1=formal
DEFAULT_VERBOSITY=0.5    # 0=brief, 1=detailed
DEFAULT_PROACTIVITY=0.5  # 0=reactive, 1=proactive

# Trust evolution rate (how quickly trust increases)
TRUST_EVOLUTION_RATE=0.05
```

## Profile Categories

### Identity

Personal identification information.

| Field | Examples | Privacy Level |
|-------|----------|---------------|
| Name | "James", "Dr. Smith" | Medium |
| Nickname | "Jim", "Jamie" | Low |
| Pronouns | "he/him", "they/them" | Medium |
| Location | "Sydney", "Australia" | High |
| Timezone | "AEDT", "UTC+11" | Low |

### Preferences

Communication and interaction preferences.

| Field | Examples | Default |
|-------|----------|---------|
| Formality | Casual, Professional | 0.5 |
| Verbosity | Brief, Detailed | 0.5 |
| Coding Style | Python, TypeScript | None |
| Response Format | Markdown, Plain | Markdown |
| Explanation Depth | High-level, Detailed | Medium |

### Schedule

Work and availability patterns.

| Field | Examples |
|-------|----------|
| Work Hours | "9am-5pm weekdays" |
| Timezone | "AEDT (UTC+11)" |
| Availability | "Busy mornings" |
| Meeting Preferences | "Prefer afternoon calls" |

### Projects

Current work and interests.

| Field | Examples |
|-------|----------|
| Current Projects | "Zetherion AI", "API Migration" |
| Technologies | "Python", "Docker", "React" |
| Interests | "Machine learning", "DevOps" |
| Learning Goals | "Learning Rust", "AWS certification" |

### Relationships

Professional and team connections.

| Field | Examples |
|-------|----------|
| Manager | "Sarah" |
| Team | "Platform Engineering" |
| Direct Reports | "Alice, Bob" |
| Collaborators | "DevOps team" |

### Skills

Expertise and capabilities.

| Field | Examples |
|-------|----------|
| Languages | "Python (expert)", "Go (intermediate)" |
| Frameworks | "FastAPI", "React" |
| Domains | "Backend", "Infrastructure" |
| Certifications | "AWS Solutions Architect" |

### Goals

Objectives and deadlines.

| Field | Examples |
|-------|----------|
| Short-term | "Ship v2.0 by Friday" |
| Long-term | "Become team lead" |
| Learning | "Complete ML course" |
| Personal | "Better work-life balance" |

### Habits

Behavioral patterns and shortcuts.

| Field | Examples |
|-------|----------|
| Communication | "Prefers async" |
| Shortcuts | "Uses 'lgtm' for approval" |
| Patterns | "Reviews PRs in morning" |

## Automatic Learning

The system learns from natural conversation.

### Learning Triggers

```
User: "I prefer Python over JavaScript"
→ Learns: Preferences.coding_style = "Python" (confidence: 0.85)

User: "My name is James"
→ Learns: Identity.name = "James" (confidence: 0.95)

User: "I work from 9 to 5"
→ Learns: Schedule.work_hours = "9am-5pm" (confidence: 0.80)

User: "I'm working on the API migration project"
→ Learns: Projects.current = "API migration" (confidence: 0.75)
```

### Inference Tiers

The system uses two inference tiers:

**Tier 1 (Regex-based)**
- Fast pattern matching
- High confidence for explicit statements
- Examples: "My name is X", "I prefer Y", "I work at Z"

**Tier 2 (LLM-based, optional)**
- Deeper context understanding
- Lower confidence, requires more confirmation
- Examples: Inferred preferences from conversation style

```env
# Use only Tier 1 (faster, less resource-intensive)
PROFILE_TIER1_ONLY=true
```

## Manual Profile Management

### Viewing Your Profile

```
@Zetherion AI show my profile
```

**Output:**
```
📋 Your Profile

Identity:
  Name: James (95%)
  Location: Sydney, Australia (80%)
  Timezone: AEDT (90%)

Preferences:
  Coding Style: Python (90%)
  Verbosity: Concise (75%)
  Formality: Casual (85%)

Work:
  Role: Software Engineer (85%)
  Team: Platform (70%)
  Current Project: Zetherion AI (95%)

(Percentages indicate confidence levels)
```

### Updating Profile

**Explicit updates:**
```
@Zetherion AI update my name to James
@Zetherion AI set my timezone to AEDT
@Zetherion AI I prefer detailed explanations
@Zetherion AI my coding language is Python
```

**Updating specific fields:**
```
@Zetherion AI update my profile: role = Senior Engineer
@Zetherion AI set preference: verbosity = detailed
```

### Viewing Specific Categories

```
@Zetherion AI show my preferences
@Zetherion AI what do you know about my schedule?
@Zetherion AI show my projects
```

### Deleting Information

**Single field:**
```
@Zetherion AI forget my location
@Zetherion AI remove my manager from profile
```

**Category:**
```
@Zetherion AI clear my relationships
```

**Full profile (requires confirmation):**
```
@Zetherion AI delete my entire profile
> Are you sure? This cannot be undone. Reply 'yes' to confirm.
```

## Confidence and Confirmation

### Confidence Levels

| Level | Score | Behavior |
|-------|-------|----------|
| High | 0.9+ | Auto-applied, no confirmation |
| Medium | 0.6-0.9 | Applied, may ask confirmation |
| Low | <0.6 | Stored as pending, asks confirmation |

### Confidence Sources

| Source | Base Confidence |
|--------|-----------------|
| Explicit statement | 0.95 ("My name is James") |
| Direct answer | 0.85 ("I prefer Python") |
| Contextual mention | 0.70 ("Working on the API...") |
| Inference | 0.50 (Inferred from behavior) |

### Confirmation Flow

When confidence is low:

```
Bot: I noticed you might prefer Python for coding. Is that correct?
User: Yes
Bot: Great! I've added that to your profile.
```

Or:

```
Bot: I noticed you might prefer Python for coding. Is that correct?
User: No, I actually prefer Go
Bot: Thanks for clarifying! I've updated your preference to Go.
```

### Pending Confirmations

View pending confirmations:
```
@Zetherion AI show pending confirmations
```

**Output:**
```
Pending Profile Confirmations:

1. Location: "Melbourne, Australia" (60% confident)
   Detected from: "heading to Melbourne next week"
   ✓ Confirm | ✗ Reject | 📝 Correct

2. Project: "Data Pipeline" (55% confident)
   Detected from: "working on data stuff"
   ✓ Confirm | ✗ Reject | 📝 Correct
```

### Confidence Decay

Confidence decreases over time if not reinforced:
- Initial: Set at detection confidence
- After 30 days: -10%
- After 90 days: -20%
- Below 0.2: Automatically asks for re-confirmation

```env
# Trust evolution affects how quickly confidence grows
TRUST_EVOLUTION_RATE=0.05  # Per positive interaction
```

## Response Adaptation

### Formality Adaptation

Based on `Preferences.formality`:

**Casual (0.0-0.3):**
```
Hey! Here's the thing about that...
```

**Balanced (0.4-0.6):**
```
Here's what you need to know about that topic...
```

**Formal (0.7-1.0):**
```
I would like to provide you with information regarding...
```

### Verbosity Adaptation

Based on `Preferences.verbosity`:

**Brief (0.0-0.3):**
```
Use `git rebase -i HEAD~3` to squash commits.
```

**Balanced (0.4-0.6):**
```
To squash the last 3 commits, use interactive rebase:
`git rebase -i HEAD~3`
Then change 'pick' to 'squash' for commits to combine.
```

**Detailed (0.7-1.0):**
```
To squash commits, you'll use interactive rebase...
[Full explanation with examples, edge cases, and warnings]
```

### Technical Level

Based on `Skills` profile:

**Beginner-friendly:**
```
Docker is like a lightweight virtual machine...
```

**Technical:**
```
Docker uses Linux namespaces and cgroups for container isolation...
```

### Coding Style

Based on `Preferences.coding_style`:

If user prefers Python, code examples will be in Python when possible:
```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

## Privacy and Data Control

### Data Storage

Profile data is stored in:
- **Qdrant**: Vector embeddings for semantic search
- **SQLite**: Structured profile data

### Encryption

When enabled, sensitive profile fields are encrypted:

```env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=your-secure-passphrase
```

Encrypted fields:
- Name, location (Identity)
- All Relationships data
- Project details
- Goals content

### Data Export (GDPR)

Export all your data:

```
@Zetherion AI export my data
```

**Output:**
```
📦 Data Export

Your data has been compiled. Here's a summary:

Profile: 15 entries
Memories: 47 items
Tasks: 12 items
Calendar: 8 events

Download link: [Generated JSON file]
```

### Data Deletion

Delete all personal data:

```
@Zetherion AI delete all my data
> This will permanently delete:
> - Your profile (15 entries)
> - Your memories (47 items)
> - Your tasks (12 items)
> - Your calendar events (8 events)
>
> Type 'DELETE ALL' to confirm.
```

### Opt-Out

Disable profile learning entirely:

```env
PROFILE_INFERENCE_ENABLED=false
```

Or per-user:
```
@Zetherion AI disable profile learning
```

## Troubleshooting

### Profile Not Learning

**Check configuration:**
```env
PROFILE_INFERENCE_ENABLED=true
```

**Check logs:**
```bash
docker-compose logs zetherion-ai-bot | grep -i profile
```

### Wrong Information Learned

**Correct it:**
```
@Zetherion AI that's not right, my name is actually James
@Zetherion AI update my location to Sydney
```

**Or delete and re-add:**
```
@Zetherion AI forget my name
@Zetherion AI my name is James
```

### Too Many Confirmations

Increase auto-apply threshold:
```env
PROFILE_CONFIDENCE_THRESHOLD=0.5  # Lower = more auto-applies
```

Or use Tier 1 only:
```env
PROFILE_TIER1_ONLY=true
```

### Profile Not Affecting Responses

**Check profile is loaded:**
```
@Zetherion AI show my profile
```

**Ensure preferences are set:**
```
@Zetherion AI set my preference for detailed explanations
```

### Database Issues

**Reset profile database:**
```bash
# Backup first
docker cp zetherion-ai-bot:/app/data/profiles.db ./profiles_backup.db

# Reset
docker exec zetherion-ai-bot rm /app/data/profiles.db
docker-compose restart zetherion-ai-bot
```

## Database Schema

Profile data uses SQLite:

```sql
CREATE TABLE profiles (
    user_id TEXT PRIMARY KEY,
    data JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE profile_entries (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, category, field)
);

CREATE TABLE pending_confirmations (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    field TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
);
```

## Additional Resources

- [Features Overview](FEATURES.md) - All Phase 5+ features
- [Skills Guide](SKILLS.md) - Profile skill commands
- [Security Guide](SECURITY.md) - Encryption details
- [Configuration Reference](CONFIGURATION.md) - All settings

---

**Last Updated:** 2026-02-07
**Version:** 3.0.0 (Profile System)
