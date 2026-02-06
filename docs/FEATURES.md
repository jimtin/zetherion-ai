# Advanced Features Guide

Zetherion AI includes powerful advanced features for cost optimization, privacy, user personalization, and extensibility. This guide provides an overview of Phase 5+ features.

## Table of Contents

- [Feature Overview](#feature-overview)
- [InferenceBroker (Multi-Provider Routing)](#inferencebroker-multi-provider-routing)
- [Cost Tracking System](#cost-tracking-system)
- [Model Discovery & Registry](#model-discovery--registry)
- [Notification System](#notification-system)
- [Profile System](#profile-system)
- [Skills Framework](#skills-framework)
- [Heartbeat Scheduler](#heartbeat-scheduler)
- [Field-Level Encryption](#field-level-encryption)

## Feature Overview

| Feature | Purpose | Configuration |
|---------|---------|---------------|
| **InferenceBroker** | Smart multi-provider LLM routing | `INFERENCE_BROKER_ENABLED=true` |
| **Cost Tracking** | Monitor and budget API usage | `COST_TRACKING_ENABLED=true` |
| **Model Discovery** | Auto-discover new models | `MODEL_DISCOVERY_ENABLED=true` |
| **Notifications** | Alerts for costs, models, errors | `NOTIFICATIONS_ENABLED=true` |
| **Profile System** | User preference learning | `PROFILE_INFERENCE_ENABLED=true` |
| **Skills** | Extensible task system | Built-in, always available |
| **Heartbeat** | Proactive scheduled actions | Configured per skill |
| **Encryption** | AES-256-GCM data protection | `ENCRYPTION_ENABLED=true` |

## InferenceBroker (Multi-Provider Routing)

The InferenceBroker intelligently routes requests to the optimal LLM provider based on task type, cost, and availability.

### How It Works

1. **Task Classification**: Each query is classified by type (simple, complex, code, creative)
2. **Provider Selection**: Best provider chosen based on:
   - Task requirements (quality vs speed)
   - Provider availability and health
   - Cost optimization preferences
   - Configured tier preferences
3. **Fallback Chain**: If primary provider fails, automatically tries alternatives

### Supported Providers

| Provider | Best For | Cost | Speed |
|----------|----------|------|-------|
| **Gemini Flash** | Simple queries, routing | Free tier | Fastest |
| **Claude Sonnet** | Complex reasoning, code | $3/M tokens | Fast |
| **GPT-4o** | General tasks | $2.50/M tokens | Fast |
| **Ollama** | Privacy, offline | Free (local) | Varies |

### Configuration

```env
# Enable InferenceBroker
INFERENCE_BROKER_ENABLED=true

# Provider tier preferences (quality, balanced, fast)
ANTHROPIC_TIER=quality
OPENAI_TIER=balanced
GOOGLE_TIER=fast
```

### Task-to-Provider Mapping

| Task Type | Default Provider | Fallback |
|-----------|------------------|----------|
| Simple Query | Gemini Flash | Ollama |
| Complex Reasoning | Claude Sonnet | GPT-4o |
| Code Generation | Claude Sonnet | GPT-4o |
| Creative Writing | GPT-4o | Claude |
| Memory Operations | Gemini Flash | Ollama |

## Cost Tracking System

Monitor API usage, set budgets, and receive alerts before exceeding limits.

### Features

- **Real-time tracking**: Every API call logged with cost
- **Budget alerts**: Warnings at configurable thresholds
- **Daily/Monthly summaries**: Aggregate spending reports
- **Per-provider breakdown**: See costs by provider and task type
- **SQLite storage**: Persistent, queryable cost database

### Configuration

```env
# Enable cost tracking
COST_TRACKING_ENABLED=true

# Budget configuration
DAILY_BUDGET_USD=5.00
MONTHLY_BUDGET_USD=50.00
BUDGET_WARNING_PCT=80.0  # Alert at 80% of budget

# Storage
COST_DB_PATH=data/costs.db
```

### Budget Alerts

When spending reaches thresholds:
- **80% warning**: Notification sent, bot continues
- **100% exceeded**: Notification sent, complex queries may be limited

### Viewing Cost Reports

```bash
# View cost summary (when implemented)
docker exec zetherion-ai-bot python -c "
from zetherion_ai.costs import CostTracker
tracker = CostTracker()
print(tracker.get_daily_summary())
"
```

See [Cost Tracking Guide](COST_TRACKING.md) for detailed usage.

## Model Discovery & Registry

Automatically discover new models from provider APIs and track deprecations.

### Features

- **Auto-discovery**: Polls provider APIs every 24 hours
- **Tier classification**: Models categorized as quality/balanced/fast
- **Deprecation tracking**: Alerts when models are deprecated
- **Pricing updates**: Automatic pricing data refresh
- **New model notifications**: Alerts for newly available models

### Configuration

```env
# Enable model discovery
MODEL_DISCOVERY_ENABLED=true

# Refresh interval (hours)
MODEL_REFRESH_HOURS=24

# Notifications for model changes
NOTIFY_ON_NEW_MODELS=true
NOTIFY_ON_DEPRECATION=true
NOTIFY_ON_MISSING_PRICING=true
```

### Model Tiers

| Tier | Characteristics | Example Models |
|------|-----------------|----------------|
| **Quality** | Best results, higher cost | Claude Sonnet, GPT-4o |
| **Balanced** | Good results, moderate cost | Claude Haiku, GPT-4o-mini |
| **Fast** | Quick responses, lower cost | Gemini Flash, Ollama |

## Notification System

Receive alerts for important events via Discord.

### Notification Types

| Type | Trigger | Priority |
|------|---------|----------|
| `MODEL_DISCOVERED` | New model available | LOW |
| `MODEL_DEPRECATED` | Model being retired | HIGH |
| `BUDGET_WARNING` | 80% of budget reached | HIGH |
| `BUDGET_EXCEEDED` | 100% of budget reached | CRITICAL |
| `DAILY_SUMMARY` | End of day report | LOW |
| `RATE_LIMIT_HIT` | API rate limited | MEDIUM |
| `SYSTEM_ERROR` | Unexpected error | CRITICAL |

### Configuration

```env
# Enable notifications
NOTIFICATIONS_ENABLED=true

# Notification preferences
NOTIFY_ON_NEW_MODELS=true
NOTIFY_ON_DEPRECATION=true
NOTIFY_ON_MISSING_PRICING=false

# Daily summary
DAILY_SUMMARY_ENABLED=true
DAILY_SUMMARY_HOUR=20  # 8 PM local time
```

### Notification Channels

Notifications are sent via Discord DM to allowed users. Future versions may support:
- Slack webhooks
- Email alerts
- Custom webhooks

## Profile System

Learn user preferences and adapt responses over time.

### Features

- **Automatic extraction**: Learns from conversations
- **Confidence scoring**: Tracks certainty of learned facts
- **Confirmation workflow**: Asks to confirm uncertain information
- **Privacy controls**: Users can view, update, or delete their profile
- **GDPR compliance**: Full data export and deletion

### Profile Categories

| Category | Examples |
|----------|----------|
| **Identity** | Name, location, timezone |
| **Preferences** | Coding style, verbosity preference |
| **Schedule** | Work hours, availability |
| **Projects** | Current projects, technologies |
| **Relationships** | Team members, managers |
| **Skills** | Programming languages, expertise |
| **Goals** | Learning objectives, deadlines |
| **Habits** | Communication style, shortcuts |

### Configuration

```env
# Enable profile inference
PROFILE_INFERENCE_ENABLED=true

# Inference settings
PROFILE_CONFIDENCE_THRESHOLD=0.6  # Minimum confidence to auto-apply
PROFILE_CACHE_TTL=300  # Cache for 5 minutes

# Response customization defaults
DEFAULT_FORMALITY=0.5  # 0=casual, 1=formal
DEFAULT_VERBOSITY=0.5  # 0=brief, 1=detailed
DEFAULT_PROACTIVITY=0.5  # 0=reactive, 1=proactive
```

### User Commands

```
@Zetherion AI show my profile
@Zetherion AI update my name to James
@Zetherion AI forget my location
@Zetherion AI export my data
```

See [Profile System Guide](PROFILES.md) for detailed usage.

## Skills Framework

Extensible system for adding new capabilities.

### Built-in Skills

| Skill | Purpose | Intents |
|-------|---------|---------|
| **Task Manager** | Track tasks and todos | Create, list, complete, delete tasks |
| **Calendar** | Schedule awareness | Check availability, work hours |
| **Profile** | User preferences | View, update, export profile |

### Task Manager Examples

```
@Zetherion AI add task: Review PR #123
@Zetherion AI list my tasks
@Zetherion AI complete task 1
@Zetherion AI show task summary
```

### Calendar Examples

```
@Zetherion AI what's my schedule today?
@Zetherion AI am I free at 3pm?
@Zetherion AI when are my work hours?
```

### Skill Intents

The router classifies messages into intents:

| Intent | Examples |
|--------|----------|
| `TASK_MANAGEMENT` | "add task", "list todos", "complete" |
| `CALENDAR_QUERY` | "schedule", "free", "availability" |
| `PROFILE_QUERY` | "my profile", "update preference" |
| `SIMPLE_QUERY` | "what is...", "explain..." |
| `COMPLEX_TASK` | "analyze this code", "help me debug" |
| `MEMORY_STORE` | "remember that...", "note that..." |
| `MEMORY_RECALL` | "search for...", "what did I say about..." |

See [Skills Guide](SKILLS.md) for detailed usage and custom skill development.

## Heartbeat Scheduler

Proactive behavior system for scheduled actions.

### Features

- **Periodic execution**: Skills run on configurable intervals
- **Quiet hours**: Respects user availability
- **Priority queue**: Important actions first
- **Rate limiting**: Maximum actions per heartbeat

### Configuration

```env
# Heartbeat runs every 5 minutes by default
# Quiet hours: 10 PM to 7 AM (configurable per skill)
```

### Proactive Actions

Skills can define heartbeat actions:
- **Task reminders**: Notify about upcoming deadlines
- **Daily summaries**: Send end-of-day recaps
- **Cost alerts**: Warn about budget thresholds
- **Health checks**: Monitor system status

## Field-Level Encryption

AES-256-GCM encryption for sensitive data at rest.

### What's Encrypted

- Memory content (conversations, facts)
- Profile data (personal information)
- Task details (descriptions, notes)
- Calendar events (titles, descriptions)

### What's NOT Encrypted

- Metadata (timestamps, IDs, user IDs)
- Vector embeddings (required for search)
- Configuration data

### Configuration

```env
# Enable encryption
ENCRYPTION_ENABLED=true

# Strong passphrase (minimum 16 characters)
ENCRYPTION_PASSPHRASE=your-very-secure-passphrase-here

# Salt file location
ENCRYPTION_SALT_PATH=data/.encryption_salt
```

### Security Notes

- **Passphrase storage**: Keep backup in secure location (not git)
- **Key rotation**: Requires data migration (planned feature)
- **Recovery**: Cannot decrypt without passphrase
- **Performance**: Minimal overhead (~1-2ms per operation)

See [Security Guide](SECURITY.md) for comprehensive security documentation.

## Enabling All Features

For maximum functionality, enable all Phase 5+ features:

```env
# Core features
INFERENCE_BROKER_ENABLED=true
COST_TRACKING_ENABLED=true
MODEL_DISCOVERY_ENABLED=true
NOTIFICATIONS_ENABLED=true
PROFILE_INFERENCE_ENABLED=true
ENCRYPTION_ENABLED=true

# Budgets
DAILY_BUDGET_USD=10.00
MONTHLY_BUDGET_USD=100.00

# Encryption (generate with: openssl rand -base64 32)
ENCRYPTION_PASSPHRASE=your-secure-passphrase

# Notifications
NOTIFY_ON_NEW_MODELS=true
NOTIFY_ON_DEPRECATION=true
DAILY_SUMMARY_ENABLED=true
```

## Feature Dependencies

Some features depend on others:

| Feature | Requires |
|---------|----------|
| Cost Tracking | InferenceBroker |
| Budget Alerts | Cost Tracking + Notifications |
| Model Notifications | Model Discovery + Notifications |
| Daily Summaries | Cost Tracking + Notifications |

## Additional Resources

- [Skills Guide](SKILLS.md) - Detailed skills documentation
- [Cost Tracking Guide](COST_TRACKING.md) - Budget management
- [Profile System Guide](PROFILES.md) - User personalization
- [Security Guide](SECURITY.md) - Encryption and security
- [Configuration Reference](CONFIGURATION.md) - All settings

---

**Last Updated:** 2026-02-07
**Version:** 3.0.0 (Phase 5+ Features)
