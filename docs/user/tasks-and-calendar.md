# Tasks and Calendar

Zetherion AI includes a built-in task manager and calendar assistant. You
interact with both through natural language in Discord -- no special syntax or
slash commands required.

---

## Task Management

The task system lets you create, track, and complete tasks without leaving
Discord. Tasks are stored per user in the Postgres database and persist across
bot restarts.

### Creating Tasks

Mention the bot and describe what you need to do. The bot parses deadlines,
priorities, and descriptions from natural language.

```
@Zetherion AI add task: Review PR #123
@Zetherion AI add task: Write unit tests by Friday
@Zetherion AI create a task to update the deployment docs by end of week
@Zetherion AI remind me to check CI results tomorrow morning
```

The bot will confirm the task was created and show you its assigned number.

### Listing Tasks

Ask the bot to show your tasks. It will return them grouped by status.

```
@Zetherion AI list my tasks
@Zetherion AI show task summary
@Zetherion AI what are my open tasks?
@Zetherion AI do I have any overdue tasks?
```

The response includes each task's number, title, priority, deadline (if set),
and current status.

### Completing Tasks

Reference a task by its number to mark it as done.

```
@Zetherion AI complete task 1
@Zetherion AI mark task 3 as done
@Zetherion AI I finished task 7
```

Completed tasks are kept in your history so you can review what you have
accomplished.

### Deleting Tasks

Remove tasks you no longer need.

```
@Zetherion AI delete task 2
@Zetherion AI remove task 5
@Zetherion AI cancel task 4
```

Deleted tasks are permanently removed and cannot be recovered.

### Task Properties

Each task can have the following properties:

| Property | Description | Example |
|---|---|---|
| Title | Short description of the task. | "Review PR #123" |
| Description | Optional longer details. | "Focus on the auth module changes." |
| Priority | Urgency level: low, medium, or high. | "high priority" |
| Deadline | Due date, parsed from natural language. | "by Friday", "tomorrow at 3pm" |
| Status | Current state of the task. | open, in progress, completed |

You can set properties when creating a task:

```
@Zetherion AI add high priority task: Fix login bug by tomorrow
@Zetherion AI create task: Update API docs, low priority, due next Monday
```

Or update them later:

```
@Zetherion AI set task 2 to high priority
@Zetherion AI change deadline for task 1 to next Wednesday
@Zetherion AI mark task 4 as in progress
```

---

## Calendar

The calendar feature lets you check your schedule and availability through
conversational queries.

### Checking Your Schedule

```
@Zetherion AI what's my schedule today?
@Zetherion AI what do I have this week?
@Zetherion AI am I free at 3pm?
@Zetherion AI do I have any meetings tomorrow?
```

The bot aggregates your tasks with deadlines and any connected calendar sources
to give you a unified view of your day or week.

### Availability Checks

When you ask if you are free at a specific time, the bot checks:

- Tasks with deadlines near that time.
- Any calendar events from connected integrations.
- Your configured work hours.

It responds with a clear yes or no and shows any conflicts.

### Work Hours

The bot can learn your typical work hours from your profile. This is used to:

- Schedule reminders during appropriate times.
- Provide context-aware responses (e.g., "You have a task due before end of
  business today").
- Avoid sending notifications outside your working hours.

To set your work hours:

```
@Zetherion AI my work hours are 9am to 5pm Monday through Friday
@Zetherion AI I usually work 8am to 6pm
```

The bot stores this in your user profile and references it going forward.

---

## Proactive Reminders

The heartbeat scheduler runs in the background and can send you reminders
without being asked. This keeps important deadlines visible without requiring
you to manually check your task list.

### What Gets Reminded

- **Upcoming deadlines** -- the bot notifies you when a task deadline is
  approaching, typically a few hours before it is due.
- **Overdue tasks** -- if a deadline passes without the task being completed,
  the bot sends a follow-up reminder.
- **Morning briefings** -- a summary of your tasks and schedule for the day,
  sent at the start of your configured work hours.

### Quiet Hours

To prevent notifications during off-hours, configure quiet hours:

```
@Zetherion AI set quiet hours from 10pm to 8am
@Zetherion AI don't send reminders on weekends
```

During quiet hours, the bot queues reminders and delivers them when quiet hours
end. No notifications will be sent during this window.

### Disabling Reminders

If you prefer to check tasks manually:

```
@Zetherion AI turn off reminders
@Zetherion AI disable proactive notifications
```

You can re-enable them at any time:

```
@Zetherion AI turn on reminders
```

---

## Runtime Google Integration Test Plan (No Restart)

This plan validates dynamic Google credential management and dynamic mailbox
monitoring (add/remove) without restarting the skills service.

### Prerequisites

- Skills service is running (default: `http://localhost:8080`).
- Work router is enabled.
- You know your Discord `user_id`.
- If `SKILLS_API_SECRET` is configured, include it in all API calls.

### 0. Set local shell variables

```bash
export SKILLS_URL="http://localhost:8080"
export SKILLS_API_SECRET="replace-if-enabled"
export USER_ID="123456789"

export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export GOOGLE_REDIRECT_URI="http://localhost:8080/gmail/callback"
```

If your local server does not enforce API-secret auth, remove the
`X-API-Secret` header from the commands below.

### 1. Set runtime OAuth config (no restart)

```bash
curl -sS -X PUT "$SKILLS_URL/settings/integrations/google_client_id" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"value\":\"$GOOGLE_CLIENT_ID\",\"changed_by\":$USER_ID,\"data_type\":\"string\"}"
```

```bash
curl -sS -X PUT "$SKILLS_URL/settings/integrations/google_redirect_uri" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"value\":\"$GOOGLE_REDIRECT_URI\",\"changed_by\":$USER_ID,\"data_type\":\"string\"}"
```

```bash
curl -sS -X PUT "$SKILLS_URL/secrets/google_client_secret" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"value\":\"$GOOGLE_CLIENT_SECRET\",\"changed_by\":$USER_ID,\"description\":\"Google OAuth client secret\"}"
```

Optional checks:

```bash
curl -sS "$SKILLS_URL/settings/integrations/google_client_id" \
  -H "X-API-Secret: $SKILLS_API_SECRET"
```

```bash
curl -sS "$SKILLS_URL/secrets" \
  -H "X-API-Secret: $SKILLS_API_SECRET"
```

### 2. Connect mailbox #1 via OAuth

```bash
curl -sS "$SKILLS_URL/oauth/google/authorize?user_id=$USER_ID" \
  -H "X-API-Secret: $SKILLS_API_SECRET"
```

Copy the `auth_url` from the response, open it in your browser, and complete
Google consent.

### 3. Verify connected mailboxes

```bash
curl -sS -X POST "$SKILLS_URL/handle" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"user_id\":\"$USER_ID\",\"intent\":\"email_status\",\"message\":\"email status\",\"context\":{\"skill_name\":\"email\",\"provider\":\"google\"}}"
```

Expected: `connected_mailboxes >= 1`.

### 4. Connect mailbox #2 (dynamic add, still no restart)

Run Step 2 again and log in with a different Google account.

Run Step 3 again and confirm mailbox count increased.

### 5. Run classification + routing from unread email

Send a test email to one connected mailbox, for example:

- Subject: `Please add task`
- Body: `Please create a task to review sprint notes tomorrow at 10am.`

Then trigger routing:

```bash
curl -sS -X POST "$SKILLS_URL/handle" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"user_id\":\"$USER_ID\",\"intent\":\"email_route\",\"message\":\"route unread email\",\"context\":{\"skill_name\":\"email\",\"provider\":\"google\",\"limit\":20}}"
```

Expected:

- `count > 0` after unread messages exist.
- `mode_counts` reflects auto/review/draft/ask/block decisions.
- If no primary task list/calendar exists, response includes primary-selection
  guidance.

### 6. Remove a monitored mailbox dynamically

```bash
export ACCOUNT_EMAIL_TO_REMOVE="you@example.com"

curl -sS -X POST "$SKILLS_URL/handle" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $SKILLS_API_SECRET" \
  -d "{\"user_id\":\"$USER_ID\",\"intent\":\"email_disconnect\",\"message\":\"disconnect $ACCOUNT_EMAIL_TO_REMOVE\",\"context\":{\"skill_name\":\"email\",\"provider\":\"google\",\"account_email\":\"$ACCOUNT_EMAIL_TO_REMOVE\"}}"
```

Run Step 3 again and confirm mailbox count decreased.

### 7. Rotate OAuth credentials at runtime (optional)

Repeat Step 1 with new credential values, then run Step 2 again. No service
restart is required.

---

## Tips

- **Natural language works best.** You do not need to memorize exact commands.
  The bot understands variations like "add a task", "create a todo", "I need to
  remember to", and similar phrasings.
- **Task numbers are stable.** A task keeps its number until it is deleted, so
  you can reference it reliably across conversations.
- **Deadlines are timezone-aware.** The bot uses the timezone from your user
  profile. Set it with: `@Zetherion AI my timezone is America/New_York`.
- **Combine with other features.** Tasks integrate with the memory system. The
  bot may reference your open tasks when answering questions about your workload
  or priorities.

---

## Related Guides

- [Getting Started](getting-started.md) -- installation and initial setup.
- [Commands](commands.md) -- full list of available commands.
- [Memory and Profiles](memory-and-profiles.md) -- how the bot stores your
  preferences, work hours, and timezone.
- [Configuration](../technical/configuration.md) -- environment variables for
  tuning reminder frequency and quiet hours.
