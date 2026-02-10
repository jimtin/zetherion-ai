# Skills Framework

## Overview

The skills framework provides an extensible system for adding capabilities to Zetherion AI. Skills are self-contained modules that handle specific domains of functionality. Each skill is registered with a central registry, served via a REST API, and invoked based on classified user intents. Skills can also perform proactive actions through a heartbeat mechanism, contribute context to the LLM system prompt, and manage their own data storage independently.

Key characteristics of a skill:

- Handles one or more specific intents (request types)
- Maintains its own persistent data storage
- Can perform proactive actions via periodic heartbeat calls
- Declares required permissions and access controls
- Contributes contextual fragments to the LLM system prompt

## Architecture

```
User Message -> Router -> Intent Classification -> Skill Registry -> Skill Handler -> Response
                                                        |
                                                   Heartbeat Scheduler
                                                   (proactive actions)
```

The skills service runs as a separate Docker container (`zetherion-ai-skills`) on port 8080, accessible only within the internal Docker network. The bot service communicates with skills via REST API calls authenticated with a shared secret.

### Component Responsibilities

| Component | Role |
|-----------|------|
| **Router** | Classifies incoming messages into intents |
| **Skill Registry** | Maintains the catalog of available skills and their intent mappings |
| **Skill Handler** | Dispatches requests to the correct skill based on intent |
| **Heartbeat Scheduler** | Periodically invokes `on_heartbeat()` on all skills for proactive behavior |

## Skill Lifecycle

Skills follow a well-defined lifecycle from registration through shutdown:

### 1. Registration

Skills register with the `SkillRegistry` during service startup. Each skill provides metadata including its name, description, version, supported intents, and required permissions.

```python
from zetherion_ai.skills.registry import SkillRegistry

registry = SkillRegistry()
registry.register(TaskManagerSkill())
registry.register(CalendarSkill())
registry.register(ProfileSkill())
registry.register(GmailSkill())
registry.register(GitHubSkill())
```

### 2. Initialization

After registration, the registry calls `initialize()` on each skill. This method performs any required setup such as connecting to databases, validating configuration, or loading cached data. A skill that returns `True` from `initialize()` transitions to `READY` status. A skill that returns `False` is marked as `FAILED` and will not receive requests.

### 3. Handling

During normal operation, the router classifies incoming user messages into intents. The registry maps each intent to its owning skill and dispatches a `SkillRequest`. The skill processes the request and returns a `SkillResponse`.

### 4. Heartbeat

The heartbeat scheduler periodically calls `on_heartbeat()` on all ready skills, passing the list of active user IDs. Skills can return `HeartbeatAction` objects representing proactive messages or notifications to send. The scheduler respects quiet hours and rate limits.

### 5. Cleanup

On service shutdown, `cleanup()` is called on each skill, allowing it to release resources, close connections, and flush any pending data.

## Built-in Skills

### Task Manager

Manages tasks, todos, and projects with priorities, deadlines, and status tracking.

- **Intents:** `create_task`, `list_tasks`, `complete_task`, `delete_task`, `task_summary`
- **Features:** Priority levels (CRITICAL, HIGH, MEDIUM, LOW), deadline tracking, status states (BACKLOG, TODO, IN_PROGRESS, BLOCKED, DONE, CANCELLED), project grouping, tag support
- **Storage:** Qdrant collection `skill_tasks` with vector embeddings for semantic search
- **Heartbeat:** Sends overdue task reminders and deadline approaching alerts

### Calendar

Provides schedule awareness and availability checking. Currently operates in awareness mode, learning from conversation context rather than syncing with external calendar services.

- **Intents:** `check_schedule`, `work_hours`, `availability`
- **Features:** Work hours tracking, availability checking, event reminders, recurring pattern detection, conflict detection
- **Integration:** Uses user profile working hours for schedule-aware behavior
- **Storage:** Qdrant collection for events and schedule data

### Profile

Manages user preferences, personal information, and learned context with confidence scoring.

- **Intents:** `show_profile`, `update_profile`, `delete_profile`, `export_data`
- **Features:** 8-category learning system (Identity, Preferences, Schedule, Projects, Relationships, Skills, Goals, Habits), confidence scoring (0.0-1.0), GDPR-compliant data export
- **Storage:** SQLite for structured profile data, Qdrant for semantic profile search
- **Confidence Thresholds:** 0.9+ auto-applied, 0.6-0.9 may ask confirmation, below 0.6 always asks confirmation

### Gmail

Multi-account email management with progressive trust and reply automation.

- **Intents:** `email_check`, `email_unread`, `email_drafts`, `email_digest`, `email_status`, `email_search`, `email_calendar`
- **Features:** Multi-account support, progressive trust levels, automated reply drafting, email digest generation, calendar event extraction from emails
- **Storage:** PostgreSQL for email metadata and account configuration
- **See:** [gmail-architecture.md](gmail-architecture.md) for detailed architecture

### GitHub

Repository management with configurable autonomy levels and pending action confirmation.

- **Intents:** `list_issues`, `get_issue`, `create_issue`, `update_issue`, `close_issue`, `reopen_issue`, `add_label`, `remove_label`, `add_comment`, `list_prs`, `get_pr`, `get_pr_diff`, `merge_pr`, `list_workflows`, `rerun_workflow`, `get_repo_info`, `set_autonomy`, `get_autonomy`
- **Features:** Configurable autonomy levels (manual, semi-auto, full-auto), pending action queue with user confirmation, event-driven notifications, repository information caching
- **Storage:** Qdrant collections for issue and PR context
- **See:** [user/github-integration.md](../user/github-integration.md) for user-facing documentation

## Skill Base Class

All skills inherit from the `Skill` base class, which defines the interface that the registry and handler depend on:

```python
class Skill:
    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata: name, description, version, permissions, intents."""
        ...

    async def initialize(self) -> bool:
        """Perform setup. Return True on success, False on failure."""
        ...

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle an intent-matched request and return a response."""
        ...

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Return proactive actions for the given users. Called periodically."""
        ...

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return a context string to include in the LLM system prompt, or None."""
        ...

    async def cleanup(self) -> None:
        """Release resources on shutdown."""
        ...
```

### SkillMetadata

```python
@dataclass
class SkillMetadata:
    name: str                    # Unique skill identifier (e.g., "task_manager")
    description: str             # Human-readable description
    version: str                 # Semantic version (e.g., "1.0.0")
    intents: list[str]           # Intents this skill handles
    permissions: list[str]       # Required permissions
```

## SkillRequest and SkillResponse

### SkillRequest

Represents an incoming request dispatched to a skill by the handler:

```python
@dataclass
class SkillRequest:
    id: UUID                     # Unique request identifier
    skill_name: str              # Target skill name
    intent: str                  # Classified intent
    user_id: str                 # Requesting user's ID
    message: str                 # Original user message
    context: dict[str, Any]      # Additional context (channel_id, guild_id, etc.)
```

### SkillResponse

Represents the result returned by a skill after processing a request:

```python
@dataclass
class SkillResponse:
    request_id: UUID             # Matches the originating SkillRequest.id
    success: bool = True         # Whether the request was handled successfully
    message: str = ""            # Human-readable response message
    data: dict = {}              # Structured response data
    error: str | None = None     # Error description if success is False
```

## Permissions

Skills declare required permissions in their metadata. The registry enforces these permissions before dispatching requests. Available permissions:

| Permission | Description |
|------------|-------------|
| `READ_MEMORIES` | Access stored memories in Qdrant |
| `WRITE_MEMORIES` | Store new data in Qdrant |
| `READ_PROFILE` | Access user profile information |
| `SEND_MESSAGES` | Send messages to Discord channels |

### Declaring Permissions

```python
from zetherion_ai.skills.permissions import Permission, PermissionSet

class MySkill(Skill):
    @property
    def required_permissions(self) -> PermissionSet:
        return PermissionSet([
            Permission.READ_MEMORIES,
            Permission.WRITE_MEMORIES,
            Permission.SEND_MESSAGES,
        ])
```

## Heartbeat System

The heartbeat system enables skills to perform proactive actions without waiting for user input. The scheduler calls `on_heartbeat()` on all ready skills at a configurable interval.

### HeartbeatAction

```python
@dataclass
class HeartbeatAction:
    skill_name: str              # Originating skill
    action_type: str             # Type of action (e.g., "send_message")
    user_id: str                 # Target user
    data: dict                   # Action-specific payload
    priority: int                # 1 = highest, 10 = lowest
```

### Behavior and Constraints

- Actions are sorted by priority before execution (1 is highest, 10 is lowest)
- The scheduler respects quiet hours derived from the user's configured working hours
- Rate limiting caps the maximum number of actions per heartbeat cycle
- If multiple skills produce actions for the same user, they are interleaved by priority

### Common Heartbeat Use Cases

| Skill | Action | Priority |
|-------|--------|----------|
| Task Manager | Overdue task reminders | 2 |
| Task Manager | Deadline approaching alerts (24h) | 4 |
| Gmail | Unread email digest | 3 |
| Gmail | High-priority email notification | 1 |
| Calendar | Upcoming meeting reminder | 2 |

## Prompt Fragments

Skills contribute real-time context to the LLM system prompt via `get_system_prompt_fragment()`. This allows the LLM to be aware of the current state of each skill without requiring explicit user queries.

### Examples

```
[GitHub: 2 action(s) pending confirmation]
[Tasks: 3 open, 1 overdue]
[Gmail: 5 unread across 2 accounts]
[Calendar: Next meeting in 45 minutes - Sprint Review]
```

The bot aggregates all non-None fragments and injects them into the system prompt before each LLM call. This gives the model awareness of pending actions, outstanding tasks, and other skill state.

## Intent Routing

The router classifies user messages into intents and maps them to skills. Classification uses a combination of keyword matching and LLM-based intent detection for ambiguous messages.

### Intent-to-Skill Mapping

| Intent Pattern | Skill | Example Messages |
|----------------|-------|------------------|
| `create_task`, `list_tasks`, `complete_task`, `delete_task`, `task_summary` | TaskManager | "add task: review PR", "what are my tasks?" |
| `check_schedule`, `work_hours`, `availability` | Calendar | "am I free at 3pm?", "show my schedule" |
| `show_profile`, `update_profile`, `delete_profile`, `export_data` | Profile | "show my profile", "export my data" |
| `email_check`, `email_unread`, `email_drafts`, `email_digest`, `email_status`, `email_search`, `email_calendar` | Gmail | "check my email", "email digest" |
| `list_issues`, `create_issue`, `get_pr`, `merge_pr`, `list_workflows`, etc. | GitHub | "list open issues", "merge PR #42" |
| `SIMPLE_QUERY` | Agent (direct) | "what is a binary tree?" |
| `COMPLEX_TASK` | Agent (complex) | "help me debug this error" |
| `MEMORY_STORE` | Memory | "remember that I prefer Python" |
| `MEMORY_RECALL` | Memory | "what did I say about the API?" |

### Routing Configuration

The skills service URL and authentication are configured via environment variables:

```env
# Skills service URL (Docker internal network)
SKILLS_SERVICE_URL=http://zetherion-ai-skills:8080

# API authentication
SKILLS_API_SECRET=your-secret-here

# Request timeout in seconds
SKILLS_REQUEST_TIMEOUT=30
```

## Creating Custom Skills

To add a new skill to Zetherion AI:

1. Create a class that inherits from `Skill`
2. Implement the required methods (`metadata`, `initialize`, `handle`)
3. Optionally implement `on_heartbeat`, `get_system_prompt_fragment`, and `cleanup`
4. Register the skill with the `SkillRegistry` in the service startup code
5. Add intent keywords to the router configuration if using keyword-based classification

```python
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse

class WeatherSkill(Skill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="weather",
            description="Check weather conditions and forecasts",
            version="1.0.0",
            intents=["check_weather", "weather_forecast"],
            permissions=["SEND_MESSAGES"],
        )

    async def initialize(self) -> bool:
        # Validate API key, warm up caches, etc.
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        if request.intent == "check_weather":
            result = await self._get_current_weather(request.message)
        else:
            result = await self._get_forecast(request.message)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=result,
        )

    async def cleanup(self) -> None:
        pass
```

For detailed guidance on building and testing custom skills, see [Adding a Skill](../development/adding-a-skill.md).

## Related Docs

- [api-reference.md](api-reference.md) -- REST API endpoints for the skills service
- [architecture.md](architecture.md) -- Overall system architecture
- [Adding a Skill](../development/adding-a-skill.md) -- Step-by-step guide for creating new skills
- [gmail-architecture.md](gmail-architecture.md) -- Gmail skill deep dive
- [security.md](security.md) -- Authentication and access control

---

**Last Updated:** 2026-02-10
**Version:** 4.0.0 (Skills Framework)
