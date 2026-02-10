# Skills REST API Reference

## Overview

The Skills Service exposes a REST API on port 8080, accessible only within the internal Docker network. This API is consumed by the bot service to dispatch skill requests, trigger heartbeat actions, manage users, and query skill state. All endpoints except `/health` require authentication via the `X-API-Secret` header.

**Base URL:** `http://zetherion-ai-skills:8080` (Docker internal network only)

## Authentication

All endpoints except `/health` require the `X-API-Secret` header. The value is compared against the configured secret using HMAC constant-time comparison to prevent timing attacks.

```
X-API-Secret: your-skills-api-secret
```

Configure the secret via the `SKILLS_API_SECRET` environment variable in your `.env` file. Requests without a valid secret receive a `401 Unauthorized` response.

---

## Health and Status

### GET /health

Returns service health status. Does not require authentication. Used by Docker HEALTHCHECK and load balancers.

**Response 200:**

```json
{
  "status": "healthy",
  "skills_ready": 5,
  "skills_total": 5
}
```

If any skills failed to initialize, `skills_ready` will be less than `skills_total`. The endpoint still returns 200 as long as the service itself is running.

---

### GET /status

Returns detailed status information for each registered skill.

**Response 200:**

```json
{
  "status": "running",
  "skills": {
    "task_manager": "ready",
    "calendar": "ready",
    "profile": "ready",
    "gmail": "ready",
    "github_management": "ready"
  }
}
```

Possible skill statuses: `ready`, `initializing`, `failed`, `disabled`.

---

### GET /skills

List all registered skills with their metadata.

**Response 200:**

```json
{
  "skills": [
    {
      "name": "task_manager",
      "description": "Track tasks and todos",
      "version": "1.0.0",
      "intents": ["create_task", "list_tasks", "complete_task", "delete_task", "task_summary"]
    },
    {
      "name": "gmail",
      "description": "Email management with multi-account support",
      "version": "1.0.0",
      "intents": ["email_check", "email_unread", "email_drafts", "email_digest", "email_status", "email_search", "email_calendar"]
    },
    {
      "name": "github_management",
      "description": "GitHub repository management with configurable autonomy",
      "version": "1.0.0",
      "intents": ["list_issues", "get_issue", "create_issue", "update_issue", "close_issue", "reopen_issue", "add_label", "remove_label", "add_comment", "list_prs", "get_pr", "get_pr_diff", "merge_pr", "list_workflows", "rerun_workflow", "get_repo_info", "set_autonomy", "get_autonomy"]
    }
  ]
}
```

---

### GET /skills/{name}

Get metadata for a specific skill by name.

**Response 200:**

```json
{
  "name": "task_manager",
  "description": "Track tasks and todos",
  "version": "1.0.0",
  "intents": ["create_task", "list_tasks", "complete_task", "delete_task", "task_summary"]
}
```

**Response 404:**

```json
{
  "error": "Skill not found: unknown_skill"
}
```

---

## Skill Handling

### POST /handle

Execute a skill request. This is the primary endpoint used by the bot service to dispatch classified user intents to the appropriate skill.

**Request:**

```json
{
  "skill_name": "gmail",
  "user_id": "123456789",
  "params": {
    "intent": "email_check",
    "message": "check my email"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `skill_name` | string | Yes | Target skill identifier |
| `user_id` | string | Yes | Discord user ID of the requester |
| `params.intent` | string | Yes | Classified intent name |
| `params.message` | string | Yes | Original user message |

**Response 200:**

```json
{
  "skill_name": "gmail",
  "status": "success",
  "result": {
    "message": "You have 3 new emails since your last check.",
    "data": {
      "total_emails": 15,
      "unread_count": 3
    }
  }
}
```

**Response 404 (skill not found):**

```json
{
  "error": "Skill not found: unknown_skill"
}
```

**Response 500 (skill error):**

```json
{
  "error": "Internal server error",
  "detail": "Gmail API connection timed out"
}
```

---

### POST /heartbeat

Trigger heartbeat actions for the specified users. The scheduler calls this endpoint periodically. Each skill's `on_heartbeat()` method is invoked, and resulting actions are aggregated and returned sorted by priority.

**Request:**

```json
{
  "user_ids": ["123456789", "987654321"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_ids` | list[string] | Yes | Discord user IDs to generate actions for |

**Response 200:**

```json
{
  "actions": [
    {
      "skill_name": "gmail",
      "action_type": "send_message",
      "user_id": "123456789",
      "data": {
        "type": "email_digest",
        "summary": "3 unread emails in your primary inbox"
      },
      "priority": 3
    },
    {
      "skill_name": "task_manager",
      "action_type": "send_message",
      "user_id": "123456789",
      "data": {
        "type": "overdue_reminder",
        "task_count": 2
      },
      "priority": 2
    }
  ]
}
```

Actions are returned sorted by priority (1 = highest). The bot service is responsible for executing the actions (e.g., sending Discord messages) and respecting quiet hours.

---

## Intent and Context

### GET /intents

List all registered intents and their skill mappings. Useful for debugging routing and verifying that skills are correctly registered.

**Response 200:**

```json
{
  "intents": {
    "email_check": "gmail",
    "email_unread": "gmail",
    "email_drafts": "gmail",
    "email_digest": "gmail",
    "email_status": "gmail",
    "email_search": "gmail",
    "email_calendar": "gmail",
    "list_issues": "github_management",
    "get_issue": "github_management",
    "create_issue": "github_management",
    "create_task": "task_manager",
    "list_tasks": "task_manager",
    "complete_task": "task_manager",
    "delete_task": "task_manager",
    "task_summary": "task_manager",
    "check_schedule": "calendar",
    "work_hours": "calendar",
    "availability": "calendar",
    "show_profile": "profile",
    "update_profile": "profile",
    "delete_profile": "profile",
    "export_data": "profile"
  }
}
```

---

### GET /prompt-fragments

Get system prompt context contributions from all skills for a specific user.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | The user ID to generate fragments for |

**Example:** `GET /prompt-fragments?user_id=123456789`

**Response 200:**

```json
{
  "fragments": [
    "[GitHub: 2 action(s) pending confirmation]",
    "[Tasks: 3 open, 1 overdue]",
    "[Gmail: 5 unread across 2 accounts]",
    "[Calendar: Next meeting in 45 minutes]"
  ]
}
```

Skills that return `None` from `get_system_prompt_fragment()` are excluded from the response.

---

## User Management

### GET /users

List registered users. Supports optional role filtering.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `role` | string | No | Filter by role (`admin`, `user`) |

**Example:** `GET /users?role=admin`

**Response 200:**

```json
{
  "users": [
    {
      "user_id": "123456789",
      "role": "admin",
      "added_at": "2026-01-15T10:30:00Z",
      "added_by": "system"
    },
    {
      "user_id": "987654321",
      "role": "user",
      "added_at": "2026-02-01T14:00:00Z",
      "added_by": "123456789"
    }
  ]
}
```

---

### POST /users

Add a new user to the system.

**Request:**

```json
{
  "user_id": "123456789",
  "role": "user",
  "added_by": "987654321"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | Yes | Discord user ID to add |
| `role` | string | Yes | Role assignment (`admin` or `user`) |
| `added_by` | string | Yes | Discord user ID of the admin performing the action |

**Response 201:**

```json
{
  "ok": true
}
```

**Response 409 (user already exists):**

```json
{
  "error": "User already exists: 123456789"
}
```

---

### DELETE /users/{user_id}

Remove a user from the system.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `removed_by` | string | Yes | Discord user ID of the admin performing the removal |

**Example:** `DELETE /users/123456789?removed_by=987654321`

**Response 200:**

```json
{
  "ok": true
}
```

**Response 404:**

```json
{
  "error": "User not found: 123456789"
}
```

---

### PATCH /users/{user_id}/role

Change a user's role.

**Request:**

```json
{
  "role": "admin",
  "changed_by": "987654321"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | New role (`admin` or `user`) |
| `changed_by` | string | Yes | Discord user ID of the admin making the change |

**Response 200:**

```json
{
  "ok": true,
  "user_id": "123456789",
  "role": "admin"
}
```

---

### GET /users/audit

Get recent audit log entries for user management actions.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | integer | No | Maximum entries to return (default: 50, max: 500) |

**Example:** `GET /users/audit?limit=50`

**Response 200:**

```json
{
  "entries": [
    {
      "timestamp": "2026-02-07T14:30:00Z",
      "action": "role_change",
      "user_id": "123456789",
      "performed_by": "987654321",
      "details": {
        "old_role": "user",
        "new_role": "admin"
      }
    },
    {
      "timestamp": "2026-02-07T10:00:00Z",
      "action": "user_added",
      "user_id": "555666777",
      "performed_by": "987654321",
      "details": {
        "role": "user"
      }
    }
  ]
}
```

---

## Settings Management

### GET /settings

List all settings. Supports optional namespace filtering.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `namespace` | string | No | Filter by settings namespace (e.g., `gmail`, `github`, `cost_tracking`) |

**Example:** `GET /settings?namespace=gmail`

**Response 200:**

```json
{
  "settings": [
    {
      "namespace": "gmail",
      "key": "digest_enabled",
      "value": "true",
      "data_type": "bool",
      "updated_at": "2026-02-07T10:00:00Z",
      "updated_by": "123456789"
    },
    {
      "namespace": "gmail",
      "key": "digest_hour",
      "value": "9",
      "data_type": "int",
      "updated_at": "2026-02-05T08:00:00Z",
      "updated_by": "123456789"
    }
  ]
}
```

---

### GET /settings/{namespace}/{key}

Get a specific setting value.

**Example:** `GET /settings/gmail/digest_enabled`

**Response 200:**

```json
{
  "namespace": "gmail",
  "key": "digest_enabled",
  "value": "true",
  "data_type": "bool",
  "updated_at": "2026-02-07T10:00:00Z",
  "updated_by": "123456789"
}
```

**Response 404:**

```json
{
  "error": "Setting not found: gmail/unknown_key"
}
```

---

### PUT /settings/{namespace}/{key}

Update a setting value. Creates the setting if it does not exist.

**Request:**

```json
{
  "value": "true",
  "changed_by": "123456789",
  "data_type": "bool"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `value` | string | Yes | Setting value (stored as string, interpreted by `data_type`) |
| `changed_by` | string | Yes | Discord user ID making the change |
| `data_type` | string | Yes | Value type: `str`, `int`, `float`, `bool`, `json` |

**Response 200:**

```json
{
  "ok": true,
  "namespace": "gmail",
  "key": "digest_enabled",
  "value": "true"
}
```

---

### DELETE /settings/{namespace}/{key}

Remove a setting override, reverting to its default value.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `deleted_by` | string | Yes | Discord user ID performing the deletion |

**Example:** `DELETE /settings/gmail/digest_enabled?deleted_by=123456789`

**Response 200:**

```json
{
  "ok": true,
  "reverted_to_default": true
}
```

**Response 404:**

```json
{
  "error": "Setting not found: gmail/unknown_key"
}
```

---

## Error Responses

All error responses follow a consistent format:

| Status Code | Meaning | Example |
|-------------|---------|---------|
| 401 | Missing or invalid `X-API-Secret` header | `{"error": "Unauthorized"}` |
| 404 | Requested resource not found | `{"error": "Skill not found: xyz"}` |
| 409 | Conflict (e.g., duplicate user) | `{"error": "User already exists: 123456789"}` |
| 422 | Invalid request body | `{"error": "Validation error", "detail": "skill_name is required"}` |
| 500 | Internal server error | `{"error": "Internal server error", "detail": "Connection refused"}` |

All 4xx and 5xx responses include an `error` field. The `detail` field is included when additional context is available.

---

## Rate Limits

The API does not enforce its own rate limits, but the bot service applies rate limiting at the user level before making API calls. Refer to the bot configuration for rate limit settings:

```env
RATE_LIMIT_MESSAGES=5
RATE_LIMIT_WINDOW=60
```

---

## Related Docs

- [skills-framework.md](skills-framework.md) -- Skill architecture, lifecycle, and development guide
- [architecture.md](architecture.md) -- Overall system architecture and service topology
- [security.md](security.md) -- Authentication, secrets management, and access control

---

**Last Updated:** 2026-02-10
**Version:** 4.0.0 (Skills REST API)
