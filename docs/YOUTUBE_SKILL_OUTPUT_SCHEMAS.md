# YouTube Skill Output Schemas

Reference for website developers consuming the YouTube skills API.

All endpoints return JSON. Dates are ISO 8601. UUIDs are v4.

---

## Intelligence Report

Returned by `GET /api/v1/youtube/channels/{ch}/intelligence`.

```json
{
  "report_id": "uuid",
  "channel_id": "uuid",
  "generated_at": "ISO8601",
  "overview": {
    "channel_name": "str",
    "subscriber_count": 10000,
    "total_views": 500000,
    "video_count": 100,
    "growth_trend": "increasing|stable|declining",
    "growth_rate_percent": 5.2,
    "period": { "from": "ISO8601", "to": "ISO8601" }
  },
  "content_performance": {
    "top_performing": [
      {
        "video_id": "str",
        "title": "str",
        "views": 15000,
        "engagement_rate": 0.038,
        "why": "str"
      }
    ],
    "underperforming": [
      {
        "video_id": "str",
        "title": "str",
        "views": 200,
        "issues": ["str"]
      }
    ],
    "categories": [
      {
        "name": "tutorials",
        "video_count": 30,
        "avg_views": 8000,
        "trend": "growing"
      }
    ],
    "optimal_length_minutes": { "min": 8, "max": 15, "sweet_spot": 12 },
    "best_posting_times": [{ "day": "Tuesday", "hour_utc": 14 }]
  },
  "audience": {
    "sentiment": { "positive": 0.65, "neutral": 0.25, "negative": 0.10 },
    "top_requests": [
      { "topic": "str", "mentions": 25, "sentiment": "positive" }
    ],
    "unanswered_questions": [{ "question": "str", "frequency": 12 }],
    "complaints": [
      { "issue": "str", "frequency": 8, "severity": "medium" }
    ]
  },
  "recommendations": [
    {
      "priority": "high|medium|low",
      "category": "content|engagement|seo|schedule|community",
      "action": "str",
      "rationale": "str",
      "expected_impact": "high|medium|low",
      "effort": "low|medium|high"
    }
  ]
}
```

---

## Reply Draft

Returned by `GET /api/v1/youtube/channels/{ch}/management/replies`.

```json
{
  "reply_id": "uuid",
  "comment_id": "str",
  "video_id": "str",
  "original_comment": "str",
  "draft_reply": "str",
  "confidence": 0.85,
  "category": "thank_you|faq|question|feedback|complaint",
  "status": "pending|approved|rejected|posted",
  "auto_approved": false,
  "model_used": "str",
  "created_at": "ISO8601"
}
```

### Updating a Reply

`PATCH /api/v1/youtube/channels/{ch}/management/replies/{id}`

Body:
```json
{ "action": "approve|reject|posted" }
```

---

## Management State

Returned by `GET /api/v1/youtube/channels/{ch}/management`.

```json
{
  "channel_id": "uuid",
  "updated_at": "ISO8601",
  "onboarding_complete": true,
  "trust": {
    "level": 1,
    "label": "GUIDED",
    "stats": {
      "total": 150,
      "approved": 145,
      "rejected": 5,
      "rate": 0.967
    },
    "next_level_at": 200
  },
  "auto_reply": {
    "enabled": true,
    "auto_categories": ["thank_you", "faq"],
    "review_categories": ["complaint"],
    "pending_count": 3,
    "posted_today": 12
  },
  "health_issues": [
    {
      "type": "str",
      "severity": "low|medium|high",
      "suggestion": "str"
    }
  ]
}
```

---

## Strategy Document

Returned by `GET /api/v1/youtube/channels/{ch}/strategy`.

```json
{
  "strategy_id": "uuid",
  "channel_id": "uuid",
  "generated_at": "ISO8601",
  "valid_until": "ISO8601",
  "executive_summary": "str",
  "positioning": {
    "niche": "str",
    "target_audience": "str",
    "value_proposition": "str",
    "tone": "str"
  },
  "content_strategy": {
    "pillars": [
      { "name": "str", "percentage": 40, "rationale": "str" }
    ],
    "calendar": [
      {
        "week": 1,
        "day": "Monday",
        "type": "str",
        "topic": "str",
        "title": "str",
        "tags": ["str"]
      }
    ],
    "series_ideas": [
      {
        "name": "str",
        "description": "str",
        "episodes": 10,
        "frequency": "weekly"
      }
    ]
  },
  "growth_tactics": [
    {
      "tactic": "str",
      "priority": "high",
      "impact": "str",
      "effort": "low|medium|high",
      "timeline": "str"
    }
  ],
  "seo": {
    "title_patterns": ["str"],
    "description_template": "str",
    "tag_strategy": "str"
  },
  "kpis": [
    {
      "metric": "str",
      "current": 10000,
      "target_30d": 11000,
      "target_90d": 15000
    }
  ]
}
```

---

## Tag Recommendation

Returned by `GET /api/v1/youtube/channels/{ch}/management/tags`.

```json
{
  "id": "uuid",
  "channel_id": "uuid",
  "video_id": "str|null",
  "current_tags": ["str"],
  "suggested_tags": ["str"],
  "reason": "str",
  "status": "pending|applied|dismissed",
  "created_at": "ISO8601"
}
```

---

## Channel Assumption

Returned by `GET /api/v1/youtube/channels/{ch}/assumptions`.

```json
{
  "id": "uuid",
  "channel_id": "uuid",
  "category": "audience|content|tone|schedule|topic|competitor|performance",
  "statement": "str",
  "evidence": ["str"],
  "confidence": 0.85,
  "source": "confirmed|inferred|invalidated",
  "confirmed_at": "ISO8601|null",
  "last_validated": "ISO8601",
  "next_validation": "ISO8601"
}
```

### Updating an Assumption

`PATCH /api/v1/youtube/channels/{ch}/assumptions/{id}`

Body:
```json
{ "action": "confirm|invalidate" }
```

---

## Channel Health Audit

Returned by `GET /api/v1/youtube/channels/{ch}/management/health`.

```json
{
  "channel_id": "uuid",
  "audit_date": "ISO8601",
  "issues": [
    {
      "type": "str",
      "severity": "low|medium|high",
      "current_state": "str",
      "suggestion": "str"
    }
  ],
  "score": 85
}
```

---

## Data Ingestion Endpoints

### Push Videos

`POST /api/v1/youtube/channels/{ch}/videos`

```json
{
  "videos": [
    {
      "video_youtube_id": "str",
      "title": "str",
      "description": "str",
      "tags": ["str"],
      "stats": { "views": 1000, "likes": 50, "comments": 10 },
      "published_at": "ISO8601"
    }
  ]
}
```

### Push Comments

`POST /api/v1/youtube/channels/{ch}/comments`

```json
{
  "comments": [
    {
      "video_youtube_id": "str",
      "comment_youtube_id": "str",
      "author": "str",
      "text": "str",
      "like_count": 5,
      "published_at": "ISO8601",
      "parent_comment_id": "str|null"
    }
  ]
}
```

### Push Stats Snapshot

`POST /api/v1/youtube/channels/{ch}/stats`

```json
{
  "snapshot": {
    "subscriber_count": 10000,
    "total_views": 500000,
    "video_count": 100
  }
}
```

### Push Document

`POST /api/v1/youtube/channels/{ch}/documents`

```json
{
  "title": "Brand Guidelines",
  "content": "Full text content...",
  "doc_type": "brand_guide|audience_research|style_guide|other"
}
```

---

## Common Patterns

### Authentication

All endpoints require `X-API-Key` header with a valid tenant API key.

### Error Responses

```json
{
  "error": "Human-readable error message"
}
```

HTTP status codes: `400` (bad request), `401` (unauthorized), `403` (forbidden), `404` (not found), `503` (service unavailable).

### Trust Levels

| Level | Label | Behavior |
|-------|-------|----------|
| 0 | SUPERVISED | All replies need human approval |
| 1 | GUIDED | Routine replies auto-approved |
| 2 | AUTONOMOUS | Most auto-approved; flagged need review |
| 3 | FULL_AUTO | All auto-approved; retroactive review |
