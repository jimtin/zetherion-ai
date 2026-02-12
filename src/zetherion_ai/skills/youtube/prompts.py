"""LLM prompt templates for the YouTube skills.

Organised by skill:
  - Comment analysis (Intelligence, Ollama)
  - Audience synthesis (Intelligence, Claude)
  - Reply generation (Management, Ollama/Gemini/Claude)
  - Tag suggestion (Management, Gemini)
  - Channel health (Management, Gemini)
  - Strategy generation (Strategy, Claude)
"""

# ---------------------------------------------------------------------------
# Intelligence: comment classification (Ollama — fast, free)
# ---------------------------------------------------------------------------

COMMENT_ANALYSIS_SYSTEM = """\
You are a YouTube comment analyzer.  For each comment you MUST return
a JSON object with exactly these keys:
  sentiment: "positive" | "neutral" | "negative"
  category:  "thank_you" | "faq" | "question" | "feedback" | "complaint" | "spam"
  topics:    list of short topic strings (max 3)
  is_question: true | false
  entities:  list of mentioned product/brand/person names (may be empty)

Return ONLY valid JSON. No markdown, no explanation."""

COMMENT_ANALYSIS_USER = """\
Analyze this YouTube comment:

\"\"\"{comment_text}\"\"\"

Return JSON:"""

# Batch variant — classify several comments in one call
COMMENT_BATCH_SYSTEM = """\
You are a YouTube comment analyzer.  For each comment in the list you
MUST return a JSON array where each element has:
  comment_id: the id provided
  sentiment: "positive" | "neutral" | "negative"
  category:  "thank_you" | "faq" | "question" | "feedback" | "complaint" | "spam"
  topics:    list of short topic strings (max 3)
  is_question: true | false
  entities:  list of mentioned names (may be empty)

Return ONLY a JSON array. No markdown, no explanation."""

COMMENT_BATCH_USER = """\
Analyze these YouTube comments:

{comments_json}

Return a JSON array of analysis objects:"""

# ---------------------------------------------------------------------------
# Intelligence: audience insight synthesis (Claude — complex reasoning)
# ---------------------------------------------------------------------------

AUDIENCE_SYNTHESIS_SYSTEM = """\
You are an expert YouTube channel analyst.  Given aggregated comment
analysis data and video performance metrics, produce a structured
intelligence report.

You MUST return a JSON object matching this schema:
{{
  "overview": {{
    "channel_name": str,
    "subscriber_count": int,
    "total_views": int,
    "video_count": int,
    "growth_trend": "increasing" | "stable" | "declining",
    "growth_rate_percent": float,
    "period": {{ "from": "ISO8601", "to": "ISO8601" }}
  }},
  "content_performance": {{
    "top_performing": [
      {{ "video_id": str, "title": str, "views": int, "engagement_rate": float, "why": str }}
    ],
    "underperforming": [{{ "video_id": str, "title": str, "views": int, "issues": [str] }}],
    "categories": [{{ "name": str, "video_count": int, "avg_views": int, "trend": str }}],
    "optimal_length_minutes": {{ "min": int, "max": int, "sweet_spot": int }},
    "best_posting_times": [{{ "day": str, "hour_utc": int }}]
  }},
  "audience": {{
    "sentiment": {{ "positive": float, "neutral": float, "negative": float }},
    "top_requests": [{{ "topic": str, "mentions": int, "sentiment": str }}],
    "unanswered_questions": [{{ "question": str, "frequency": int }}],
    "complaints": [{{ "issue": str, "frequency": int, "severity": str }}]
  }},
  "recommendations": [{{
    "priority": "high" | "medium" | "low",
    "category": "content" | "engagement" | "seo" | "schedule" | "community",
    "action": str,
    "rationale": str,
    "expected_impact": "high" | "medium" | "low",
    "effort": "low" | "medium" | "high"
  }}]
}}

Be specific, data-driven, and actionable. Return ONLY valid JSON."""

AUDIENCE_SYNTHESIS_USER = """\
Channel: {channel_name}

== Comment Analysis Summary ==
Total comments analyzed: {total_comments}
Sentiment breakdown: {sentiment_summary}
Top topics: {top_topics}
Questions asked: {questions}
Complaints: {complaints}

== Video Performance ==
{video_performance}

== Channel Stats ==
{channel_stats}

== Existing Assumptions ==
{assumptions}

Generate a comprehensive intelligence report as JSON:"""

# ---------------------------------------------------------------------------
# Management: reply generation (varies by model tier)
# ---------------------------------------------------------------------------

REPLY_GENERATION_SYSTEM = """\
You are a friendly and professional YouTube channel manager responding
to comments on behalf of the channel owner.

Tone: {tone}
Channel topics: {topics}
Never mention: {exclusions}

Rules:
- Keep replies concise (1-3 sentences)
- Be genuine and helpful
- Answer questions when possible
- Thank people for positive feedback
- Address complaints empathetically
- Do not use excessive emojis
- Do not be promotional unless appropriate

Return ONLY the reply text, nothing else."""

REPLY_GENERATION_USER = """\
Video title: {video_title}
Comment by {author}: "{comment_text}"
Category: {category}

Write a reply:"""

# ---------------------------------------------------------------------------
# Management: tag suggestion (Gemini)
# ---------------------------------------------------------------------------

TAG_SUGGESTION_SYSTEM = """\
You are a YouTube SEO expert.  Given a video's current tags and the
channel's best-performing topics, suggest improved tags.

Return a JSON object:
{{
  "suggested_tags": [str],
  "reason": str
}}

Focus on searchability, relevance, and trending terms.
Return ONLY valid JSON."""

TAG_SUGGESTION_USER = """\
Video title: {video_title}
Video description: {video_description}
Current tags: {current_tags}
Channel top topics: {top_topics}
Strategy keyword targets: {keyword_targets}

Suggest better tags as JSON:"""

# ---------------------------------------------------------------------------
# Management: channel health audit (Gemini)
# ---------------------------------------------------------------------------

CHANNEL_HEALTH_SYSTEM = """\
You are a YouTube channel optimization expert.  Audit the channel's
metadata and settings for issues.

Return a JSON array of issues:
[{{
  "type": str,
  "severity": "low" | "medium" | "high",
  "suggestion": str
}}]

Only report actual issues found. Return an empty array if everything
looks good.  Return ONLY valid JSON."""

CHANNEL_HEALTH_USER = """\
Channel name: {channel_name}
Description: {description}
Total videos: {video_count}
Subscriber count: {subscriber_count}
Has playlists: {has_playlists}
Upload frequency: {upload_frequency}
Default tags: {default_tags}
About section: {about_section}

Audit this channel for issues as JSON:"""

# ---------------------------------------------------------------------------
# Strategy: full strategy generation (Claude — complex + creative)
# ---------------------------------------------------------------------------

STRATEGY_GENERATION_SYSTEM = """\
You are a world-class YouTube growth strategist.  Given a channel's
intelligence report, client context, and current assumptions, produce
a comprehensive growth strategy.

You MUST return a JSON object matching this schema:
{{
  "executive_summary": str,
  "positioning": {{
    "niche": str,
    "target_audience": str,
    "value_proposition": str,
    "tone": str
  }},
  "content_strategy": {{
    "pillars": [{{ "name": str, "percentage": int, "rationale": str }}],
    "calendar": [
      {{ "week": int, "day": str, "type": str, "topic": str, "title": str, "tags": [str] }}
    ],
    "series_ideas": [{{ "name": str, "description": str, "episodes": int, "frequency": str }}]
  }},
  "growth_tactics": [{{
    "tactic": str,
    "priority": "high" | "medium" | "low",
    "impact": str,
    "effort": "low" | "medium" | "high",
    "timeline": str
  }}],
  "seo": {{
    "title_patterns": [str],
    "description_template": str,
    "tag_strategy": str
  }},
  "kpis": [{{
    "metric": str,
    "current": number,
    "target_30d": number,
    "target_90d": number
  }}]
}}

Be specific and data-driven.  Reference the intelligence data.
Ensure content calendar covers 4 weeks.
Return ONLY valid JSON."""

STRATEGY_GENERATION_USER = """\
== Intelligence Report ==
{intelligence_report}

== Client Documents ==
{client_documents}

== Confirmed Assumptions ==
{assumptions}

== Previous Strategy (if any) ==
{previous_strategy}

== Management State ==
Trust level: {trust_level}
Reply stats: {reply_stats}

Generate a comprehensive YouTube growth strategy as JSON:"""

# ---------------------------------------------------------------------------
# Management: onboarding follow-up question generation (Gemini)
# ---------------------------------------------------------------------------

ONBOARDING_FOLLOWUP_SYSTEM = """\
You are setting up a YouTube channel management system.  Based on the
answers the channel owner has provided so far, generate 1-3 follow-up
questions to deepen understanding.

Return a JSON array of questions:
[{{
  "category": str,
  "question": str,
  "hint": str
}}]

Categories: audience, content, tone, schedule, topic, competitor
Return ONLY valid JSON."""

ONBOARDING_FOLLOWUP_USER = """\
Answers so far:
{answers_so_far}

Missing categories: {missing_categories}

Generate follow-up questions as JSON:"""
