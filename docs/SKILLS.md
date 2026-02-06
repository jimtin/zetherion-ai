# Skills Framework Guide

The Skills Framework provides extensible capabilities for Zetherion AI, including task management, calendar awareness, and user profile management.

## Table of Contents

- [Overview](#overview)
- [Built-in Skills](#built-in-skills)
- [Task Manager Skill](#task-manager-skill)
- [Calendar Skill](#calendar-skill)
- [Profile Skill](#profile-skill)
- [Skill Intents](#skill-intents)
- [Skills Service Architecture](#skills-service-architecture)
- [Creating Custom Skills](#creating-custom-skills)

## Overview

Skills are modular components that extend Zetherion AI's capabilities. Each skill:

- Handles specific types of requests (intents)
- Maintains its own data storage
- Can perform proactive actions via heartbeat
- Has defined permissions and access controls

### Architecture

```
User Message → Router → Intent Classification → Skill Handler → Response
                                    ↓
                              Skill Service (REST API)
                                    ↓
                              Skill Registry
                                    ↓
                        Task Manager / Calendar / Profile
```

## Built-in Skills

| Skill | Description | Intents |
|-------|-------------|---------|
| **Task Manager** | Track tasks, todos, and projects | TASK_MANAGEMENT |
| **Calendar** | Schedule awareness and availability | CALENDAR_QUERY |
| **Profile** | User preference management | PROFILE_QUERY |

## Task Manager Skill

Manage tasks, todos, and projects with priorities and deadlines.

### Task Properties

| Property | Description | Values |
|----------|-------------|--------|
| **Title** | Task description | Any text |
| **Status** | Current state | BACKLOG, TODO, IN_PROGRESS, BLOCKED, DONE, CANCELLED |
| **Priority** | Importance level | CRITICAL, HIGH, MEDIUM, LOW |
| **Project** | Grouping category | Any text or null |
| **Deadline** | Due date | ISO date or null |
| **Tags** | Labels | List of strings |

### Commands

#### Creating Tasks

```
@Zetherion AI add task: Review PR #123
@Zetherion AI create task: Update documentation with high priority
@Zetherion AI new task: Deploy to production by Friday
@Zetherion AI add task: Fix login bug for project AuthSystem
```

**Parsing:**
- Priority detected from keywords: "high priority", "urgent", "critical"
- Deadlines detected: "by Friday", "due tomorrow", "before next week"
- Projects detected: "for project X", "in project Y"

#### Listing Tasks

```
@Zetherion AI list my tasks
@Zetherion AI show tasks for project AuthSystem
@Zetherion AI what are my high priority tasks?
@Zetherion AI show blocked tasks
```

**Output:**
```
Your Tasks (5 total):

📋 TODO:
  1. [HIGH] Review PR #123
  2. [MEDIUM] Update documentation

🔄 IN PROGRESS:
  3. [HIGH] Fix login bug (AuthSystem)

🚫 BLOCKED:
  4. [CRITICAL] Deploy to production - waiting on QA

✅ DONE (today):
  5. [LOW] Update README
```

#### Completing Tasks

```
@Zetherion AI complete task 1
@Zetherion AI mark task "Review PR" as done
@Zetherion AI finish task 3
```

#### Updating Tasks

```
@Zetherion AI update task 1 priority to critical
@Zetherion AI move task 2 to blocked
@Zetherion AI set deadline for task 1 to tomorrow
@Zetherion AI add tag "urgent" to task 3
```

#### Deleting Tasks

```
@Zetherion AI delete task 5
@Zetherion AI remove completed tasks
@Zetherion AI clear all tasks (requires confirmation)
```

#### Task Summary

```
@Zetherion AI task summary
@Zetherion AI show my task stats
```

**Output:**
```
Task Summary:
  Total: 15 tasks
  - Backlog: 3
  - Todo: 5
  - In Progress: 4
  - Blocked: 1
  - Done (this week): 2

  By Priority:
  - Critical: 1
  - High: 4
  - Medium: 7
  - Low: 3

  Overdue: 2 tasks
  Due Today: 1 task
```

### Data Storage

Tasks are stored in Qdrant collection `skill_tasks` with vector embeddings for semantic search.

## Calendar Skill

Schedule awareness and availability checking. Currently operates in "awareness mode" - learning from conversation rather than syncing with external calendars.

### Features

- **Work hours tracking**: Knows your typical schedule
- **Availability checking**: Answers "am I free at X?"
- **Event reminders**: Proactive notifications
- **Recurring patterns**: Understands daily/weekly patterns

### Event Properties

| Property | Description | Values |
|----------|-------------|--------|
| **Title** | Event name | Any text |
| **Type** | Event category | MEETING, DEADLINE, REMINDER, WORK_HOURS, BREAK, FOCUS_TIME, PERSONAL |
| **Start/End** | Time range | ISO datetime |
| **Recurrence** | Repeat pattern | DAILY, WEEKLY, BIWEEKLY, MONTHLY, YEARLY, WEEKDAYS |
| **Location** | Where | Any text or null |

### Commands

#### Checking Schedule

```
@Zetherion AI what's my schedule today?
@Zetherion AI show my calendar for this week
@Zetherion AI what meetings do I have tomorrow?
```

**Output:**
```
Today's Schedule (Mon, Feb 7):

09:00 - 09:30  Team standup (recurring)
10:00 - 11:00  Sprint planning
12:00 - 13:00  Lunch break
14:00 - 15:00  1:1 with manager
16:00 - 17:00  Focus time (blocked)

Available slots: 11:00-12:00, 13:00-14:00, 15:00-16:00
```

#### Checking Availability

```
@Zetherion AI am I free at 3pm?
@Zetherion AI can I schedule a meeting tomorrow at 10?
@Zetherion AI when am I available this afternoon?
```

**Output:**
```
At 3:00 PM today:
❌ Busy - "Sprint review" (14:30-15:30)

Next available slot: 15:30-17:00 (1.5 hours)
```

#### Work Hours

```
@Zetherion AI when are my work hours?
@Zetherion AI what time do I usually start?
@Zetherion AI set my work hours to 9am-5pm
```

#### Adding Events (Awareness Mode)

```
@Zetherion AI remember I have a meeting at 2pm tomorrow
@Zetherion AI note that I'm on PTO next Friday
@Zetherion AI I have standup every day at 9am
```

### Future: Calendar Integration

Planned integrations (not yet implemented):
- Google Calendar sync
- Microsoft Outlook sync
- Apple Calendar sync
- iCal import

## Profile Skill

Manage user preferences and personal information.

### Profile Categories

| Category | Examples | Usage |
|----------|----------|-------|
| **Identity** | Name, location, timezone | Personalization |
| **Preferences** | Coding style, verbosity | Response adaptation |
| **Schedule** | Work hours, availability | Calendar awareness |
| **Projects** | Current work, technologies | Context |
| **Relationships** | Team, manager, reports | Communication |
| **Skills** | Languages, expertise | Recommendations |
| **Goals** | Learning, deadlines | Motivation |
| **Habits** | Shortcuts, patterns | Efficiency |

### Commands

#### Viewing Profile

```
@Zetherion AI show my profile
@Zetherion AI what do you know about me?
@Zetherion AI show my preferences
```

**Output:**
```
Your Profile:

📋 Identity:
  Name: James (confidence: 95%)
  Location: Sydney, Australia (confidence: 80%)
  Timezone: AEDT (confidence: 90%)

💼 Work:
  Role: Software Engineer (confidence: 85%)
  Team: Platform (confidence: 70%)
  Manager: Sarah (confidence: 60%)

⚙️ Preferences:
  Coding Style: Python preferred (confidence: 90%)
  Verbosity: Concise (confidence: 75%)
  Formality: Casual (confidence: 85%)

🎯 Current Projects:
  - Zetherion AI (confidence: 95%)
  - API Migration (confidence: 70%)

Last updated: 2 hours ago
```

#### Updating Profile

```
@Zetherion AI update my name to James
@Zetherion AI my timezone is AEDT
@Zetherion AI I prefer detailed explanations
@Zetherion AI set my coding style to Python
```

#### Deleting Information

```
@Zetherion AI forget my location
@Zetherion AI remove my manager from profile
@Zetherion AI clear my profile (requires confirmation)
```

#### Confidence Tracking

Profile entries have confidence scores (0-1):
- **0.9+**: High confidence, auto-applied
- **0.6-0.9**: Medium confidence, may ask confirmation
- **<0.6**: Low confidence, always asks confirmation

```
@Zetherion AI show profile confidence
@Zetherion AI which profile items are uncertain?
```

#### Data Export (GDPR)

```
@Zetherion AI export my data
@Zetherion AI download my profile
```

Exports JSON file with all stored data.

## Skill Intents

The router classifies messages into intents to route to the correct skill.

### Intent Detection

| Intent | Keywords/Patterns | Skill |
|--------|-------------------|-------|
| `TASK_MANAGEMENT` | add task, create todo, list tasks, complete, delete task | Task Manager |
| `CALENDAR_QUERY` | schedule, free, available, meeting, calendar, work hours | Calendar |
| `PROFILE_QUERY` | my profile, update preference, forget, export data | Profile |
| `SIMPLE_QUERY` | what is, explain, tell me about | Agent (direct) |
| `COMPLEX_TASK` | analyze, help me, debug, review | Agent (complex) |
| `MEMORY_STORE` | remember, note that, save | Memory |
| `MEMORY_RECALL` | search for, find, what did I say | Memory |

### Intent Examples

```python
# Task Management
"add task: review PR" → TASK_MANAGEMENT
"what tasks do I have?" → TASK_MANAGEMENT
"complete task 1" → TASK_MANAGEMENT

# Calendar
"am I free at 3?" → CALENDAR_QUERY
"what's my schedule?" → CALENDAR_QUERY
"show my work hours" → CALENDAR_QUERY

# Profile
"show my profile" → PROFILE_QUERY
"update my name" → PROFILE_QUERY
"export my data" → PROFILE_QUERY
```

## Skills Service Architecture

Skills run as a separate Docker service for isolation and scalability.

### Service Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/skills` | GET | List available skills |
| `/handle` | POST | Handle skill request |
| `/heartbeat` | POST | Trigger heartbeat actions |
| `/status` | GET | Skill status and metrics |

### Request Format

```json
{
  "skill_id": "task_manager",
  "intent": "TASK_MANAGEMENT",
  "user_id": "123456789",
  "message": "add task: review PR #123",
  "context": {
    "channel_id": "987654321",
    "guild_id": "111222333"
  }
}
```

### Response Format

```json
{
  "status": "success",
  "skill_id": "task_manager",
  "response": "Created task: 'review PR #123' with HIGH priority",
  "data": {
    "task_id": "task_abc123",
    "title": "review PR #123",
    "priority": "HIGH",
    "status": "TODO"
  }
}
```

### Configuration

```env
# Skills service URL (Docker internal)
SKILLS_SERVICE_URL=http://zetherion-ai-skills:8080

# API authentication
SKILLS_API_SECRET=your-secret-here

# Request timeout
SKILLS_REQUEST_TIMEOUT=30
```

## Creating Custom Skills

Extend Zetherion AI with custom skills.

### Skill Base Class

```python
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse

class MyCustomSkill(Skill):
    """Custom skill implementation."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="my_custom_skill",
            name="My Custom Skill",
            description="Does something useful",
            version="1.0.0",
            intents=["MY_CUSTOM_INTENT"],
        )

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle incoming request."""
        # Parse message and perform action
        result = await self.do_something(request.message)

        return SkillResponse(
            status="success",
            response=f"Result: {result}",
        )

    async def heartbeat(self) -> list[HeartbeatAction]:
        """Return proactive actions (optional)."""
        return []
```

### Registering Skills

```python
from zetherion_ai.skills.registry import SkillRegistry

registry = SkillRegistry()
registry.register(MyCustomSkill())
```

### Skill Permissions

Skills can require specific permissions:

```python
from zetherion_ai.skills.permissions import Permission, PermissionSet

class MySkill(Skill):
    @property
    def required_permissions(self) -> PermissionSet:
        return PermissionSet([
            Permission.READ_MESSAGES,
            Permission.SEND_MESSAGES,
            Permission.MANAGE_TASKS,  # Custom permission
        ])
```

### Data Storage

Skills use Qdrant collections for persistent storage:

```python
async def store_data(self, data: dict):
    await self.memory.store(
        collection="my_skill_data",
        content=data["content"],
        metadata={"user_id": data["user_id"]},
    )

async def search_data(self, query: str):
    return await self.memory.search(
        collection="my_skill_data",
        query=query,
        limit=10,
    )
```

## Troubleshooting

### Skill Not Responding

```bash
# Check skills service health
curl http://localhost:8080/health

# View skills service logs
docker-compose logs zetherion-ai-skills

# Restart skills service
docker-compose restart zetherion-ai-skills
```

### Intent Not Recognized

The router may not recognize custom intents. Check:
1. Intent keywords in router configuration
2. Skill registration in registry
3. Message format matches expected patterns

### Task/Event Not Saved

Check Qdrant connection:
```bash
# Verify Qdrant is healthy
curl http://localhost:6333/healthz

# Check collections exist
curl http://localhost:6333/collections
```

## Additional Resources

- [Features Overview](FEATURES.md) - All Phase 5+ features
- [Architecture](ARCHITECTURE.md) - System design
- [Configuration](CONFIGURATION.md) - Environment variables
- [Troubleshooting](TROUBLESHOOTING.md) - Common issues

---

**Last Updated:** 2026-02-07
**Version:** 3.0.0 (Skills Framework)
