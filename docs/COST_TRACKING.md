# Cost Tracking Guide

Monitor, budget, and optimize your LLM API spending with Zetherion AI's built-in cost tracking system.

## Table of Contents

- [Overview](#overview)
- [Configuration](#configuration)
- [Understanding Costs](#understanding-costs)
- [Budget Management](#budget-management)
- [Cost Reports](#cost-reports)
- [Optimization Strategies](#optimization-strategies)
- [Troubleshooting](#troubleshooting)

## Overview

The cost tracking system monitors every API call to LLM providers and:

- **Tracks spending** in real-time per provider and task type
- **Enforces budgets** with configurable daily and monthly limits
- **Sends alerts** before you exceed thresholds
- **Generates reports** for spending analysis
- **Stores history** in SQLite for querying

### How It Works

```
API Request → InferenceBroker → Cost Calculation → Budget Check → Storage
                                      ↓
                              Alert if threshold reached
```

## Configuration

### Enable Cost Tracking

```env
# Enable the cost tracking system
COST_TRACKING_ENABLED=true

# Storage location
COST_DB_PATH=data/costs.db
```

### Set Budgets

```env
# Daily spending limit (USD)
DAILY_BUDGET_USD=5.00

# Monthly spending limit (USD)
MONTHLY_BUDGET_USD=50.00

# Warning threshold (percentage)
BUDGET_WARNING_PCT=80.0
```

### Enable Notifications

```env
# Enable notifications for cost alerts
NOTIFICATIONS_ENABLED=true

# Daily summary at 8 PM
DAILY_SUMMARY_ENABLED=true
DAILY_SUMMARY_HOUR=20
```

## Understanding Costs

### Provider Pricing

| Provider | Model | Input (per 1M tokens) | Output (per 1M tokens) |
|----------|-------|----------------------|------------------------|
| **Anthropic** | Claude Sonnet 4.5 | $3.00 | $15.00 |
| **Anthropic** | Claude Haiku 4.5 | $0.25 | $1.25 |
| **OpenAI** | GPT-4o | $2.50 | $10.00 |
| **OpenAI** | GPT-4o-mini | $0.15 | $0.60 |
| **Google** | Gemini Flash | Free tier | Free tier |
| **Ollama** | Local models | $0.00 | $0.00 |

*Prices as of February 2026. Check provider websites for current rates.*

### Token Estimation

Approximate token counts:
- **1 word** ≈ 1.3 tokens
- **100 words** ≈ 130 tokens
- **1 page of text** ≈ 500-700 tokens

### Typical Query Costs

| Query Type | Input Tokens | Output Tokens | Estimated Cost (Claude) |
|------------|-------------|---------------|------------------------|
| Simple question | ~50 | ~100 | $0.0016 |
| Code review | ~500 | ~300 | $0.006 |
| Complex analysis | ~1000 | ~500 | $0.01 |
| Long conversation | ~2000 | ~1000 | $0.02 |

### Cost by Task Type

The system tracks costs by task type:

| Task Type | Typical Provider | Avg Cost/Query |
|-----------|------------------|----------------|
| Simple Query | Gemini Flash | $0.00 (free) |
| Memory Search | Gemini Flash | $0.00 (free) |
| Complex Reasoning | Claude Sonnet | $0.005 |
| Code Generation | Claude Sonnet | $0.008 |
| Creative Writing | GPT-4o | $0.006 |

## Budget Management

### Daily Budgets

Daily budgets reset at midnight (local time).

**Workflow:**
1. Each API call adds to daily total
2. At 80% (configurable), warning notification sent
3. At 100%, exceeded notification sent
4. Complex queries may be limited (uses cheaper provider)

### Monthly Budgets

Monthly budgets reset on the 1st of each month.

**Workflow:**
1. Each API call adds to monthly total
2. At 80%, warning notification sent
3. At 100%, exceeded notification sent
4. Daily budget may be reduced automatically

### Budget Notifications

```
⚠️ Budget Warning (80% reached)

Daily Spending: $4.00 / $5.00 (80%)
Monthly Spending: $35.00 / $50.00 (70%)

Top spending:
- Claude Sonnet: $2.50 (62%)
- GPT-4o: $1.20 (30%)
- Gemini Flash: $0.00 (0%)

Remaining today: $1.00
```

### Budget Actions

When budgets are exceeded:

| Level | Action |
|-------|--------|
| **80% Warning** | Notification only, no restrictions |
| **100% Daily** | Prefer cheaper providers, limit complex queries |
| **100% Monthly** | Strict limits, may require manual override |

## Cost Reports

### Daily Summary

Automatically sent at configured hour (default: 8 PM):

```
📊 Daily Cost Summary (Feb 7, 2026)

Total Spent Today: $3.45

By Provider:
  Claude Sonnet: $2.10 (61%)
  GPT-4o: $1.05 (30%)
  Gemini Flash: $0.00 (0%)
  Ollama: $0.00 (0%)

By Task Type:
  Complex Reasoning: $1.50 (43%)
  Code Generation: $1.20 (35%)
  Simple Queries: $0.00 (0%)
  Memory Operations: $0.00 (0%)

Queries: 47 total
  - Successful: 45 (96%)
  - Rate Limited: 2 (4%)

Budget Status:
  Daily: $3.45 / $5.00 (69%)
  Monthly: $28.45 / $50.00 (57%)
```

### Monthly Summary

Sent on the 1st of each month:

```
📊 Monthly Cost Summary (January 2026)

Total Spent: $42.50

By Provider:
  Claude Sonnet: $28.00 (66%)
  GPT-4o: $12.50 (29%)
  Gemini Flash: $0.00 (0%)
  Ollama: $0.00 (0%)

Daily Average: $1.37
Peak Day: Jan 15 ($4.80)
Lowest Day: Jan 3 ($0.12)

Total Queries: 1,247
Average Cost/Query: $0.034

Budget: $42.50 / $50.00 (85%)
```

### Querying Cost Data

Access cost data programmatically:

```bash
# View SQLite database
docker exec zetherion-ai-bot sqlite3 /app/data/costs.db "
  SELECT date(timestamp), SUM(cost_usd) as daily_cost
  FROM usage_records
  GROUP BY date(timestamp)
  ORDER BY date(timestamp) DESC
  LIMIT 7;
"
```

### Export Cost Data

```bash
# Export to CSV
docker exec zetherion-ai-bot sqlite3 -header -csv /app/data/costs.db "
  SELECT * FROM usage_records
  WHERE timestamp > datetime('now', '-30 days');
" > costs_last_30_days.csv
```

## Optimization Strategies

### 1. Use Free Tiers Effectively

**Gemini Flash Free Tier:**
- 15 requests/minute
- 1,500 requests/day
- Use for routing, simple queries, embeddings

**Strategy:**
```env
# Route simple queries to Gemini
ROUTER_BACKEND=gemini
```

### 2. Optimize Context Window

Reduce tokens sent per request:

```env
# Fewer messages in context
CONTEXT_WINDOW_SIZE=5  # Default: 10

# Fewer memory search results
MEMORY_SEARCH_LIMIT=3  # Default: 5
```

**Savings:** ~30-50% reduction in input tokens

### 3. Use Ollama for Routing

Local routing = zero API costs:

```env
ROUTER_BACKEND=ollama
OLLAMA_ROUTER_MODEL=llama3.1:8b
```

**Savings:** 100% of routing costs (typically 10-20% of total)

### 4. Choose Cost-Effective Models

| Need | Expensive | Cost-Effective |
|------|-----------|----------------|
| Simple Q&A | Claude Sonnet | Gemini Flash |
| Code Review | GPT-4o | Claude Haiku |
| Summarization | Claude Sonnet | GPT-4o-mini |

### 5. Rate Limiting

Prevent runaway costs:

```env
# Limit messages per user
RATE_LIMIT_MESSAGES=5
RATE_LIMIT_WINDOW=60
```

### 6. Hybrid Approach

Best of both worlds:

```env
# Free routing
ROUTER_BACKEND=gemini

# Quality for complex tasks only
ANTHROPIC_API_KEY=sk-ant-...

# Skip OpenAI (redundant with Claude)
OPENAI_API_KEY=

# Strict budget
DAILY_BUDGET_USD=3.00
```

**Expected Monthly Cost:** $20-40

### Cost Comparison: Strategies

| Strategy | Monthly Cost | Quality | Privacy |
|----------|-------------|---------|---------|
| All Cloud (Claude + GPT-4o) | $50-100 | Best | Low |
| Gemini Only | $0 (free tier) | Good | Low |
| Ollama Only | $0 (electricity) | Good | High |
| Hybrid (Gemini + Claude) | $20-40 | Very Good | Medium |
| Hybrid (Ollama + Claude) | $15-30 | Very Good | High |

## Troubleshooting

### Costs Higher Than Expected

**Check usage patterns:**
```bash
# Top cost queries today
docker exec zetherion-ai-bot sqlite3 /app/data/costs.db "
  SELECT provider, task_type, COUNT(*) as count, SUM(cost_usd) as total
  FROM usage_records
  WHERE date(timestamp) = date('now')
  GROUP BY provider, task_type
  ORDER BY total DESC;
"
```

**Common causes:**
1. Long conversations (high context window)
2. Complex queries routed to expensive models
3. Retry loops on failed requests
4. Memory search returning too many results

### Budget Not Enforcing

**Check configuration:**
```bash
# Verify settings loaded
docker exec zetherion-ai-bot python -c "
from zetherion_ai.config import get_settings
s = get_settings()
print(f'Daily: {s.daily_budget_usd}')
print(f'Monthly: {s.monthly_budget_usd}')
print(f'Cost tracking: {s.cost_tracking_enabled}')
"
```

**Ensure InferenceBroker enabled:**
```env
INFERENCE_BROKER_ENABLED=true
COST_TRACKING_ENABLED=true
```

### Notifications Not Sending

**Check notification configuration:**
```env
NOTIFICATIONS_ENABLED=true
BUDGET_WARNING_PCT=80.0
```

**Verify Discord connection:**
```bash
docker-compose logs zetherion-ai-bot | grep -i notification
```

### Database Issues

**Reset cost database (lose history):**
```bash
docker exec zetherion-ai-bot rm /app/data/costs.db
docker-compose restart zetherion-ai-bot
```

**Backup before reset:**
```bash
docker cp zetherion-ai-bot:/app/data/costs.db ./costs_backup.db
```

## Database Schema

The cost database uses SQLite with this schema:

```sql
CREATE TABLE usage_records (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    success BOOLEAN,
    error_message TEXT
);

CREATE INDEX idx_timestamp ON usage_records(timestamp);
CREATE INDEX idx_provider ON usage_records(provider);
```

## Additional Resources

- [Features Overview](FEATURES.md) - All Phase 5+ features
- [Configuration Reference](CONFIGURATION.md) - All settings
- [InferenceBroker](FEATURES.md#inferencebroker-multi-provider-routing) - Provider routing
- [Hardware Recommendations](HARDWARE-RECOMMENDATIONS.md) - Ollama setup for $0 routing

---

**Last Updated:** 2026-02-07
**Version:** 3.0.0 (Cost Tracking)
