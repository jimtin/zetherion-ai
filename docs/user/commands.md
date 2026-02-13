# Commands Reference

Complete reference for all Zetherion AI commands. Discord is the first supported input interface, but the underlying skills and agent core are source-agnostic. This reference covers slash commands, natural language interactions, Gmail integration, GitHub integration, task management, profile management, and cost tracking.

---

## Quick Reference

| Command | Type | Description | Example |
|---------|------|-------------|---------|
| `/ask` | Slash Command | Ask a question (routes to optimal LLM) | `/ask What is Python?` |
| `/remember` | Slash Command | Store a memory | `/remember I prefer dark mode` |
| `/search` | Slash Command | Search memories | `/search preferences` |
| `/ping` | Slash Command | Check bot status and latency | `/ping` |
| `/channels` | Slash Command | List visible text channels | `/channels` |
| `/allow` | Admin Slash Command | Add user to allowlist with role | `/allow @user user` |
| `/deny` | Admin Slash Command | Remove user from allowlist | `/deny @user` |
| `/role` | Admin Slash Command | Change a user's RBAC role | `/role @user admin` |
| `/allowlist` | Admin Slash Command | List allowed users (optional role filter) | `/allowlist admin` |
| `/audit` | Admin Slash Command | Show recent audit entries | `/audit 20` |
| `/config_list` | Admin Slash Command | Show runtime settings (optional namespace) | `/config_list security` |
| `/config_set` | Admin Slash Command | Set runtime setting with type inference | `/config_set security block_threshold 0.7` |
| `/config_reset` | Admin Slash Command | Remove DB override and use default/env | `/config_reset security block_threshold` |
| DM | Direct Message | Talk naturally, no prefix needed | Just send a message |
| @mention | Server Message | Ask in a server channel | `@Zetherion AI help me` |
| "Check my email" | Natural Language | Check email inbox summary | `@Zetherion AI check my email` |
| "List issues" | Natural Language | List GitHub issues | `@Zetherion AI list issues` |
| "Add task: ..." | Natural Language | Create a new task | `@Zetherion AI add task: Review PR` |
| "Show my profile" | Natural Language | View your learned profile | `@Zetherion AI show my profile` |
| "Remember that ..." | Natural Language | Store a memory | `Remember that I use VS Code` |
| "Search for ..." | Natural Language | Semantic memory search | `Search for my project notes` |

---

## Slash Commands

### `/ask` -- Ask a Question

Ask Zetherion AI a question or request help with a task. The bot analyzes intent and complexity, then routes your query to the best-suited model.

**Syntax:**
```
/ask <question>
```

**Parameters:**
- `question` (required) -- Your question or request

**Examples:**
```
/ask What is the capital of France?
/ask Explain async/await in Python
/ask Write a function to reverse a string
/ask Help me debug this error: TypeError...
```

**Routing behavior:**

- The router classifies your query by intent and complexity, then dispatches to the provider you've configured for that task type
- You choose between local inference (Ollama) and cloud providers (Gemini, Claude, OpenAI) -- see the LLM Provider Configuration section in the README
- Routing classification can use Gemini Flash (cloud) or Ollama Llama 3.2 1B (local), depending on your setup
- The bot searches recent conversation history and relevant memories to provide context

**Expected response time:**
- Simple queries: 1--3 seconds
- Complex tasks: 5--15 seconds

---

### `/remember` -- Store a Memory

Store information in long-term memory for later retrieval.

**Syntax:**
```
/remember <content>
```

**Parameters:**
- `content` (required) -- What you want the bot to remember

**Examples:**
```
/remember I prefer dark mode in all applications
/remember My birthday is March 15th
/remember Project deadline is next Friday
```

**Behavior:**
- Content is stored in the Qdrant vector database
- Embedded using Gemini text-embedding-004 for semantic search
- Persists across bot restarts
- Automatically recalled in relevant future conversations

---

### `/search` -- Search Memories

Search your stored memories by semantic similarity.

**Syntax:**
```
/search <query>
```

**Parameters:**
- `query` (required) -- What to search for

**Examples:**
```
/search preferences
/search birthday
/search Python projects
```

**Behavior:**
- Uses vector similarity, not keyword matching
- Returns the top 5 most relevant memories with similarity scores
- Sorted by relevance

---

### `/ping` -- Check Bot Status

Verify the bot is online and check latency.

**Syntax:**
```
/ping
```

**Parameters:** None

**Expected response:**
```
Pong! Latency: 45ms
```

**Response time:** Under 500ms. The response is ephemeral (only visible to you).

---

### Administrative Slash Commands

These commands require `admin` or `owner` RBAC role.

#### `/channels`

Lists channels the bot can currently access in the guild.

#### `/allow <user> [role]`

Adds a user to the allowlist and assigns a role (`user`, `admin`, `owner`, `restricted`).

#### `/deny <user>`

Removes a user from the allowlist.

#### `/role <user> <role>`

Changes an existing user's role.

#### `/allowlist [role]`

Shows allowed users, optionally filtered by role.

#### `/audit [limit]`

Shows recent RBAC and settings audit entries.

#### `/config_list [namespace]`

Displays runtime settings currently overridden in PostgreSQL.

#### `/config_set <namespace> <key> <value>`

Creates or updates a runtime setting override.

`value` type is inferred automatically:

- `true|false|yes|no` -> boolean
- integer literals -> integer
- float/scientific literals -> float
- JSON object/array text -> json
- anything else -> string

Examples:

```text
/config_set security block_threshold 0.7
/config_set security tier2_enabled true
/config_set notifications daily_summary_hour 8
/config_set github default_repo my-org/my-repo
/config_set profile defaults {"formality":0.7,"verbosity":0.4}
```

#### `/config_reset <namespace> <key>`

Deletes the runtime override so resolution falls back to env/default values.

---

## Natural Language Commands

Zetherion AI understands natural language. You do not need to memorize specific syntax. The examples below show common phrasings, but the bot will understand reasonable variations of each.

Natural language commands work via Direct Messages (no prefix needed) or via @mention in a server channel.

---

### Asking Questions

The bot automatically detects the complexity of your question and routes it to the appropriate model.

**Simple questions** are routed to your configured fast provider (e.g. Gemini Flash or Ollama):
```
Hello!
What's 2 + 2?
Good morning
Thanks for your help!
```

**Complex questions** are routed to your configured reasoning provider (e.g. Claude, GPT, or local Ollama):
```
Write a Python function to validate email addresses
Explain how transformers work in detail
Help me design a REST API for a blog
Debug this code: [code snippet]
```

**Routing logic:**

- The router classifies your message by intent and complexity (using Gemini Flash or local Llama 3.2 1B, depending on your configuration)
- If the query is classified as complex, it is dispatched to your configured reasoning provider
- Simple queries go to your configured fast provider
- You control which providers handle which task types -- see the LLM Provider Configuration section in the README

---

### Memory Commands

**Store a memory:**
```
Remember that I prefer tabs over spaces
Note: Project uses PostgreSQL
Keep in mind that I'm in PST timezone
Don't forget my favorite color is blue
```

**Recall memories:**
```
What do you know about me?
What did we discuss yesterday?
What do you remember about my projects?
Tell me about my preferences
```

**Search memories:**
```
Search for my project notes
Search for deadlines
Search for food preferences
```

The bot uses semantic similarity search, so you do not need to use exact keywords. Asking "What are my coding preferences?" will find memories about tabs, spaces, editors, and similar topics.

---

### Gmail Commands

Zetherion AI can connect to your Gmail account to check emails, generate digests, and search your inbox from Discord. See the [Gmail Integration Guide](gmail.md) for full setup instructions.

**Check email:**
```
Check my email
```
Triggers the `email_check` intent. Shows total count, unread count, and high-priority items.

**Show unread emails:**
```
Show unread emails
Show my unread messages
```
Triggers the `email_unread` intent. Lists up to 5 unread emails with subject and sender.

**Show drafts:**
```
Show my drafts
List my email drafts
```
Triggers the `email_drafts` intent. Lists pending reply drafts awaiting your review.

**Email digest:**
```
Give me an email digest
Morning digest
Evening digest
Weekly digest
```
Triggers the `email_digest` intent. Generates a summary organized by priority and topic.

**Gmail status:**
```
Gmail status
Show Gmail connection status
```
Triggers the `email_status` intent. Shows connected accounts and last sync time.

**Search emails:**
```
Search emails for invoice
Find emails about project update
Search my email for meeting notes
```
Triggers the `email_search` intent. Searches by subject and sender across connected accounts.

---

### GitHub Commands

Zetherion AI integrates with GitHub to manage issues, pull requests, and CI workflows directly from Discord.

**List issues:**
```
List issues
Show open issues
What issues are open?
```
Triggers the `list_issues` action. Lists open issues in the configured repository.

**View a specific issue:**
```
Show issue #42
What's issue #42 about?
```
Triggers the `get_issue` action. Displays the issue title, body, labels, and status.

**Create an issue:**
```
Create issue: Fix login button alignment
Create issue: Add dark mode support
```
Triggers the `create_issue` action. Requires confirmation unless autonomy is set to autonomous for this action.

**Close an issue:**
```
Close issue #42
```
Triggers the `close_issue` action.

**List pull requests:**
```
List PRs
Show pull requests
What PRs are open?
```
Triggers the `list_prs` action.

**View a pull request:**
```
Show PR #10
What's PR #10 about?
```
Triggers the `get_pr` action. Displays the PR title, description, review status, and merge status.

**View a PR diff:**
```
Show PR diff #10
What changed in PR #10?
```
Triggers the `get_pr_diff` action. Displays the file changes in the pull request.

**Merge a pull request:**
```
Merge PR #10
```
Triggers the `merge_pr` action. This always requires explicit confirmation, regardless of autonomy settings.

**List workflows / CI status:**
```
List workflows
Show CI status
What's the build status?
```
Triggers the `list_workflows` action.

**Repository info:**
```
Repo info
Show repository information
```
Triggers the `get_repo_info` action.

**Set autonomy level:**
```
Set autonomy for create_issue to autonomous
Set autonomy for close_issue to confirm
```
Triggers the `set_autonomy` action. Controls whether actions require confirmation.

**View autonomy settings:**
```
Show autonomy settings
What are my autonomy settings?
```
Triggers the `get_autonomy` action.

---

### Task Commands

Manage personal tasks and to-do items through Discord.

**Create a task:**
```
Add task: Review PR #123
Add task: Update documentation
Create task: Deploy to staging
```

**List tasks:**
```
List my tasks
Show my to-do list
What tasks do I have?
```

**Complete a task:**
```
Complete task 1
Mark task 1 as done
Finish task 1
```

**Delete a task:**
```
Delete task 2
Remove task 2
```

---

### Profile Commands

Zetherion AI learns about you over time. You can view, update, and manage your profile data.

**View your profile:**
```
Show my profile
What do you know about me?
Display my profile info
```
Displays all learned information the bot has stored about you (name, location, preferences, and more).

**Update profile fields:**
```
Update my name to James
Set my timezone to PST
My location is London
```

**Remove profile data:**
```
Forget my location
Remove my timezone
Clear my name
```

**Export your data (GDPR):**
```
Export my data
Download my data
Give me all my data
```
Generates a full export of all data the bot holds about you.

**Delete all your data:**
```
Delete all my data
Erase everything about me
Remove all my information
```
Permanently deletes all stored data. This action requires confirmation before proceeding.

---

### Cost Commands

Cost tracking is handled automatically by the system and is not triggered by direct user commands. Costs are tracked per-model and per-user. Information about costs appears in:

- Daily summary reports (generated by the heartbeat scheduler)
- Admin dashboards and logs
- Per-request metadata (visible in debug mode)

The 6 Docker services that make up Zetherion AI each contribute to overall resource usage, and cost tracking covers all external API calls to Claude Sonnet 4.5, GPT-5.2, and Gemini 2.5 Flash.

---

## Direct Messages

You can message Zetherion AI directly for a private conversation.

**How to start a DM:**
1. Find Zetherion AI in your server member list
2. Right-click and select "Message"
3. Type your message naturally

**Key points:**
- No prefix or slash command is needed. Just type normally.
- All functionality is available: asking questions, storing memories, Gmail commands, GitHub commands, tasks, and profile management.
- Conversation history is maintained across sessions.
- DMs are more private than server messages.

**Examples:**
```
Hello!
What can you help me with?
Remember that I prefer Python 3.12
Check my email
List issues
Show my profile
```

---

## Mentions

In server channels, mention the bot to get its attention.

**Syntax:**
```
@Zetherion AI <your message>
```

**Examples:**
```
@Zetherion AI what's the best way to learn Python?
@Zetherion AI remember our team meeting is every Monday
@Zetherion AI check my email
@Zetherion AI show open issues
```

**Behavior:**
- The bot only responds when explicitly mentioned.
- The mention prefix is stripped from your message before processing.
- Responses are public (visible to everyone in the channel).
- All functionality is the same as via DM or slash commands.

**Empty mention:**
```
@Zetherion AI
```
The bot will respond with a prompt asking how it can help.

---

## Rate Limits

**Default configuration:**

| Setting | Value |
|---------|-------|
| Max messages per user | 10 |
| Time window | 60 seconds |
| Warning cooldown | 30 seconds |

**Behavior:**
1. A user can send up to 10 messages within a 60-second window.
2. The 11th message within that window is blocked, and a warning is shown.
3. Additional messages within the 30-second warning cooldown are silently blocked.
4. After 60 seconds from the first message, the counter resets.

If you are rate limited, the bot will respond with:
```
You're sending messages too quickly. Please wait a moment before trying again.
```

---

## Permissions

### Permissions the Bot Requires in Discord

| Permission | Reason |
|------------|--------|
| Send Messages | To respond to commands and queries |
| Read Message History | To load conversation context |
| Use Slash Commands | For `/ask`, `/remember`, `/search`, `/ping` |
| Embed Links | For rich formatting in responses |
| View Channels | To see channels where it is mentioned |

### Permissions Users Need

| Permission | Reason |
|------------|--------|
| Send Messages | To use any command |
| Read Message History | For context-aware conversations |
| View Channel | To interact in channels where the bot is present |

### Allowlist

By default, all users can interact with the bot. To restrict access:

```bash
# In .env -- comma-separated Discord user IDs
ALLOWED_USER_IDS=123456789,987654321
```

Leave the value empty to allow all users:
```bash
ALLOWED_USER_IDS=
```

---

## Security Notes

- All messages pass through prompt injection detection before processing.
- Unauthorized users receive a clear rejection message.
- Rate limiting is enforced per user to prevent abuse.
- Gmail OAuth tokens are stored securely and email content is encrypted at rest.
- GitHub actions that modify state (create issue, close issue, merge PR) require confirmation unless autonomy settings have been explicitly changed.

---

## Related Guides

- [Gmail Integration](gmail.md) -- Full setup and usage guide for Gmail features
- [Troubleshooting](troubleshooting.md) -- Common issues and solutions
- [FAQ](faq.md) -- Frequently asked questions
