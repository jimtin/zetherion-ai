# Observation Pipeline

## Overview
The observation pipeline passively extracts facts, preferences, and context from user interactions. It operates alongside explicit commands, building understanding without requiring users to explicitly tell the bot everything.

## Architecture
```
User Input
    |
    v
+---[Extraction]---+
|  Tier 1: Regex   |---> High confidence patterns
|  Tier 2: LLM     |---> Deeper context understanding
+--------+---------+
         |
         v
+---[Classification]---+
|  Category assignment  |
|  Confidence scoring   |
+--------+-------------+
         |
         v
+---[Storage]----------+
|  PersonalLearning    |
|  (PostgreSQL)        |
+--------+-------------+
         |
         v
+---[Integration]------+
|  Profile building    |
|  Context enrichment  |
|  Response adaptation |
+-----------------------+
```

## Extraction Tiers

### Tier 1: Pattern-Based (Regex)
Fast, high-confidence extraction for explicit statements:
- "My name is X" -> Identity.name (confidence: 0.95)
- "I prefer X" -> Preferences (confidence: 0.85)
- "I work at X" / "I'm from X" -> Identity (confidence: 0.80)
- "I'm working on X" -> Projects (confidence: 0.75)

Configure: PROFILE_TIER1_ONLY=true to disable LLM extraction.

### Tier 2: LLM-Based
Deeper understanding via InferenceBroker:
- Inferred preferences from conversation patterns
- Relationship extraction from mentions
- Schedule patterns from activity
- Lower confidence, requires confirmation

## Learning Categories
| Category | Description | Example |
|----------|-------------|---------|
| PREFERENCE | User preferences | "Prefers Python" |
| CONTACT | Relationship info | "Works with Sarah" |
| SCHEDULE | Time patterns | "Available mornings" |
| POLICY | Action permissions | "Auto-reply to team" |
| CORRECTION | User corrections | "Actually, I meant Go" |
| FACT | General knowledge | "Has AWS certification" |

## Learning Sources
| Source | Description |
|--------|-------------|
| EXPLICIT | User directly stated |
| INFERRED | Derived from behavior |
| EMAIL | Extracted from Gmail data |
| CALENDAR | From calendar events |
| DISCORD | From Discord messages |

## Confidence Scoring
- Explicit statements: 0.90-0.95
- Direct answers: 0.80-0.90
- Contextual mentions: 0.65-0.80
- Inferred from behavior: 0.40-0.60

### Confirmation Flow
- High confidence (>= 0.90): Auto-applied
- Medium confidence (0.60-0.90): Applied, may request confirmation
- Low confidence (< 0.60): Stored as pending, bot asks for confirmation
- Unconfirmed learnings expire after PROFILE_CONFIRMATION_EXPIRY_HOURS (default 72h)

## Storage
Learnings stored in PostgreSQL personal_learnings table:
```sql
personal_learnings (id, user_id, category, content, confidence, source, confirmed, created_at)
```

## Integration Points
1. **Profile Building**: Confirmed learnings update PersonalProfile
2. **Context Enrichment**: Learnings added to LLM context for personalized responses
3. **Response Adaptation**: Communication style adjusts based on learned preferences
4. **Proactive Actions**: Schedule learnings inform heartbeat timing

## Related Docs
Links to: personal-understanding.md, architecture.md, configuration.md
