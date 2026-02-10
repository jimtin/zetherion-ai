# Memory and Profiles

Zetherion AI remembers your conversations and learns your preferences over time.
The memory system stores past interactions for later retrieval, while the profile
system builds an understanding of who you are and adapts its responses
accordingly.

---

## How Memory Works

### Conversation Memory

The bot maintains context from recent messages within the current conversation
window. Beyond that window, it automatically searches past conversations for
semantically similar content to inform its responses.

All memory content is stored in a Qdrant vector database. When encryption is
enabled, memories are encrypted at rest using AES-256-GCM.

### Storing Memories

You can explicitly ask the bot to remember something. The information is
embedded as a vector and becomes searchable by semantic similarity.

```
@Zetherion AI remember I prefer Python for coding
/remember My birthday is March 15th
```

You can also store notes without using a command keyword. Any message that
contains factual information the bot recognizes as worth remembering may be
stored automatically.

```
"Note: Project uses PostgreSQL"
```

### Searching Memories

Retrieve stored memories by describing what you are looking for. The bot returns
the most relevant results ranked by similarity score.

```
/search preferences
@Zetherion AI what do you remember about my projects?
```

### Memory Encryption

When encryption is enabled, all memory content is encrypted at rest using
AES-256-GCM. Even if the underlying database is compromised, your data remains
unreadable without the encryption passphrase.

```env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=your-secure-passphrase
```

---

## Profile System

The profile system learns about you from your conversations and uses that
information to personalize responses. Learning happens both passively (from
things you say in conversation) and actively (from explicit statements).

### What Gets Learned

The bot tracks information across eight categories:

| Category | Examples |
|---|---|
| Identity | Name, location, timezone, pronouns |
| Preferences | Coding style, verbosity, formality, response format |
| Schedule | Work hours, availability, meeting preferences |
| Projects | Current work, technologies in use, interests |
| Relationships | Team members, collaborators, manager |
| Skills | Programming languages, frameworks, expertise level |
| Goals | Short-term objectives, long-term career plans, learning goals |
| Habits | Communication patterns, shortcuts, review routines |

### How Learning Works

The bot assigns a confidence score to each piece of information it learns. The
score depends on how the information was communicated:

| Source | Confidence | Example |
|---|---|---|
| Explicit statement | 95% | "My name is James" |
| Direct preference | 85% | "I prefer Python" |
| Contextual mention | 70% | "Working on the API migration..." |
| Inferred from behavior | 50% | Detected from conversation patterns |

The system uses two inference tiers. Tier 1 relies on fast regex-based pattern
matching for high-confidence extractions like explicit statements. Tier 2 uses
LLM-based analysis for deeper contextual understanding, producing lower
confidence scores that are more likely to require confirmation.

### Confidence and Confirmation

What happens with learned information depends on its confidence score:

| Confidence | Behavior |
|---|---|
| High (90% and above) | Auto-applied to your profile. No confirmation needed. |
| Medium (60% to 90%) | Applied to your profile. The bot may ask you to confirm. |
| Low (below 60%) | Stored as pending. The bot asks you to confirm before applying. |

Confidence decays over time if not reinforced. After 30 days without
reinforcement, confidence drops by 10%. After 90 days, it drops by 20%. If
confidence falls below 20%, the bot asks you to re-confirm the information.

---

## Managing Your Profile

### Viewing Your Profile

See everything the bot has learned about you, with confidence percentages for
each entry.

```
@Zetherion AI show my profile
@Zetherion AI show my preferences
@Zetherion AI what do you know about my schedule?
@Zetherion AI show my projects
```

### Updating Your Profile

Make explicit updates to any profile information. Explicit updates are stored
at high confidence.

```
@Zetherion AI update my name to James
@Zetherion AI set my timezone to AEDT
@Zetherion AI I prefer detailed explanations
@Zetherion AI my coding language is Python
```

### Deleting Information

Remove specific entries, entire categories, or your full profile.

**Single field:**

```
@Zetherion AI forget my location
@Zetherion AI remove my manager from profile
```

**Entire category:**

```
@Zetherion AI clear my relationships
```

**Full profile deletion (requires confirmation):**

```
@Zetherion AI delete my entire profile
```

The bot will ask you to confirm before permanently deleting your entire profile.

### Pending Confirmations

When the bot has learned something at low confidence, it stores the information
as a pending confirmation. You can review and respond to these:

```
@Zetherion AI show pending confirmations
```

The bot will list each pending item with its detected value, confidence score,
and the source message. You can confirm, reject, or correct each one.

---

## Response Adaptation

The bot adapts its responses based on what it has learned about you.

### Formality

Responses range from casual to formal based on your preference setting:

- **Casual:** conversational tone, contractions, relaxed phrasing.
- **Balanced:** clear and direct, neutral tone.
- **Formal:** professional language, complete sentences, no contractions.

### Verbosity

The level of detail in responses adapts to your preference:

- **Brief:** short, direct answers with minimal explanation.
- **Balanced:** answers with enough context to be useful.
- **Detailed:** thorough explanations with examples, edge cases, and caveats.

### Technical Level

Based on your skills profile, the bot adjusts how it explains concepts:

- **Beginner-friendly:** analogies and simplified explanations.
- **Technical:** assumes familiarity, uses precise terminology.

### Coding Style

When providing code examples, the bot uses your preferred programming language
when possible. If you have indicated a preference for Python, for example, code
samples will default to Python unless the context requires a different language.

---

## Observation Pipeline

Beyond explicit profile management, the bot passively extracts facts from your
conversations through an observation pipeline:

- **Tiered extraction** -- regex patterns handle high-confidence facts, while
  LLM analysis captures deeper contextual information.
- **Personal understanding model** -- builds a comprehensive picture of your
  preferences, work patterns, and interests over time.
- **Contact graph** -- tracks relationships and people you mention, connecting
  them to the appropriate profile categories.

This pipeline runs automatically. You do not need to take any action for the
bot to learn from your conversations.

---

## Privacy Controls

### View Your Data

Export all stored personal data, including your profile, memories, tasks, and
calendar events.

```
@Zetherion AI export my data
```

The bot compiles your data and provides a summary with a downloadable export.

### Delete Your Data

Permanently remove all personal data associated with your account. This
includes your profile, memories, tasks, and calendar events.

```
@Zetherion AI delete all my data
```

This action requires confirmation. The bot will ask you to verify before
proceeding. Once deleted, the data cannot be recovered.

### Disable Learning

Stop the bot from learning new information about you. Existing profile data
is preserved but no new entries are added.

```
@Zetherion AI disable profile learning
```

You can also disable learning globally through your `.env` file:

```env
PROFILE_INFERENCE_ENABLED=false
```

To re-enable learning:

```
@Zetherion AI enable profile learning
```

### Encryption

All profile data and memories can be encrypted at rest. When enabled,
sensitive fields are encrypted using AES-256-GCM before being written to the
database.

```env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=your-secure-passphrase
```

Encrypted fields include names, locations, relationship data, project details,
and goals content. Even with direct database access, encrypted data is
unreadable without the passphrase.

---

## Troubleshooting

### Profile Not Learning

Verify that profile inference is enabled:

```env
PROFILE_INFERENCE_ENABLED=true
```

Check the bot logs for profile-related errors:

```bash
docker-compose logs zetherion-ai-bot | grep -i profile
```

### Wrong Information Learned

Correct it directly:

```
@Zetherion AI that's not right, my name is actually James
@Zetherion AI update my location to Sydney
```

Or delete the incorrect entry and re-add it:

```
@Zetherion AI forget my name
@Zetherion AI my name is James
```

### Too Many Confirmation Prompts

Lower the auto-apply threshold so more information is accepted without
confirmation:

```env
PROFILE_CONFIDENCE_THRESHOLD=0.5
```

Or restrict inference to Tier 1 only, which produces fewer low-confidence
results:

```env
PROFILE_TIER1_ONLY=true
```

### Profile Not Affecting Responses

Verify your profile has been populated:

```
@Zetherion AI show my profile
```

If preferences are missing, set them explicitly:

```
@Zetherion AI set my preference for detailed explanations
```

---

## Related Guides

- [Getting Started](getting-started.md) -- installation and initial setup.
- [Commands](commands.md) -- full list of available commands.
- [Configuration](../technical/configuration.md) -- environment variables for
  profiles, memory, and encryption.
- [Security](../technical/security.md) -- encryption details and data
  protection.
- [Gmail Integration](gmail.md) -- connect your Gmail account for
  email management.
