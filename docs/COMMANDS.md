# SecureClaw Command Reference

Complete list of all Discord commands and interactions for SecureClaw.

## Quick Reference

| Command | Type | Description | Usage |
|---------|------|-------------|-------|
| `/ask` | Slash Command | Ask a question | `/ask What is Python?` |
| `/remember` | Slash Command | Store a memory | `/remember I prefer dark mode` |
| `/search` | Slash Command | Search memories | `/search preferences` |
| `/ping` | Slash Command | Check bot status | `/ping` |
| DM | Direct Message | Talk naturally | Just send a message |
| Mention | Server Message | Ask in server | `@SecureClaw help me` |

---

## Slash Commands

### `/ask` - Ask a Question

**Description:** Ask SecureClaw a question or request help with a task.

**Syntax:**
```
/ask <question>
```

**Parameters:**
- `question` (required) - Your question or request

**Examples:**
```
/ask What is the capital of France?
/ask Explain async/await in Python
/ask Write a function to reverse a string
/ask What's the weather like? (bot doesn't have real-time data)
/ask Help me debug this error: TypeError...
```

**Behavior:**
- Bot analyzes intent and complexity
- Simple questions → Answered by Gemini Flash (fast)
- Complex tasks → Routed to Claude/GPT-4 (slower, better quality)
- Searches recent conversation history and relevant memories
- Response includes context from previous interactions

**Expected Response Time:**
- Simple queries: 1-3 seconds
- Complex tasks: 5-15 seconds

**Security:**
- Checks user allowlist
- Rate limited (10 messages per minute)
- Prompt injection detection enabled

---

### `/remember` - Store a Memory

**Description:** Ask SecureClaw to remember something for later retrieval.

**Syntax:**
```
/remember <content>
```

**Parameters:**
- `content` (required) - What you want the bot to remember

**Examples:**
```
/remember I prefer dark mode in all applications
/remember My birthday is March 15th
/remember I'm a Python developer working on web apps
/remember I don't like spicy food
/remember Project deadline is next Friday
```

**Behavior:**
- Stores content in Qdrant vector database
- Content is embedded using Gemini text-embedding-004
- Searchable via semantic similarity
- Persists across bot restarts
- Automatically recalled in relevant future conversations

**Expected Response:**
```
✓ I'll remember that: "I prefer dark mode in all applications"
```

**Response Time:** < 2 seconds

**Storage:**
- Stored in: `qdrant_storage/collections/long_term_memory/`
- Persists until manually deleted
- Encrypted at rest (if Qdrant configured with encryption)

---

### `/search` - Search Memories

**Description:** Search your stored memories by semantic similarity.

**Syntax:**
```
/search <query>
```

**Parameters:**
- `query` (required) - What to search for

**Examples:**
```
/search preferences
/search birthday
/search Python projects
/search food preferences
/search deadlines
```

**Behavior:**
- Searches long-term memory collection
- Uses vector similarity (not keyword matching)
- Returns top 5 most relevant memories
- Shows similarity score (0-100%)
- Sorted by relevance

**Expected Response:**
```
**Search Results:**

1. [95%] I prefer dark mode in all applications
2. [87%] I'm a Python developer working on web apps
3. [72%] Project uses FastAPI framework
```

**Response Time:** < 1 second

**No Results Response:**
```
No matching memories found.
```

---

### `/ping` - Check Bot Status

**Description:** Verify the bot is online and check latency.

**Syntax:**
```
/ping
```

**Parameters:** None

**Expected Response:**
```
🦀 Pong! Latency: 45ms
```

**Response Time:** < 500ms

**Use Cases:**
- Verify bot is responsive
- Check connection quality
- Debug connectivity issues
- Test bot permissions

**Visibility:** Response is ephemeral (only you can see it)

---

## Direct Messaging (DM)

**Description:** Send messages directly to the bot in a private conversation.

**How to Use:**
1. Find SecureClaw in your server member list
2. Right-click → Message
3. Type your message naturally

**Examples:**
```
Hello!
What can you help me with?
Explain quantum computing
Remember that I live in San Francisco
What did we talk about yesterday?
```

**Behavior:**
- No special prefix required
- Works exactly like `/ask` command
- Full conversation history maintained
- Supports all intents (ask, remember, recall)
- More private than server messages

**Advantages:**
- No `/ask` prefix needed
- Private conversation
- Easier for testing
- Better for sensitive information

---

## Mentions in Server

**Description:** Mention the bot in any channel where it has access.

**Syntax:**
```
@SecureClaw <your message>
```

**Examples:**
```
@SecureClaw what's the best way to learn Python?
@SecureClaw can you help me debug this code?
@SecureClaw remember that our team meeting is every Monday
```

**Behavior:**
- Bot only responds when explicitly mentioned
- Removes mention from message before processing
- Public response (everyone can see)
- Same functionality as DM or `/ask`

**Empty Mention:**
```
@SecureClaw
```
Response:
```
How can I help you?
```

---

## Natural Language Intents

The bot automatically detects your intent from natural language:

### Simple Query Intent
**Triggers:** Greetings, quick facts, simple questions

**Examples:**
```
Hello!
What's 2 + 2?
Thanks for your help!
Good morning
```

**Model Used:** Gemini Flash (fast, free tier)

---

### Complex Task Intent
**Triggers:** Code generation, detailed analysis, multi-step tasks

**Examples:**
```
Write a Python function to validate email addresses
Explain how transformers work in detail
Help me design a REST API for a blog
Debug this code: [code snippet]
```

**Model Used:** Claude 3.5 Sonnet or GPT-4 (slower, better quality)

**Routing Logic:**
- Gemini Flash analyzes message
- If complexity confidence > 70% → Routes to Claude/GPT-4
- Otherwise → Gemini Flash handles it

---

### Memory Store Intent
**Triggers:** Explicit remember requests

**Examples:**
```
Remember that I prefer tabs over spaces
Note: Project uses PostgreSQL
Keep in mind that I'm in PST timezone
Don't forget my favorite color is blue
```

**Auto-detection Keywords:**
- "remember"
- "note"
- "keep in mind"
- "don't forget"

---

### Memory Recall Intent
**Triggers:** Questions about past conversations or stored info

**Examples:**
```
What do you know about me?
What did we discuss yesterday?
What are my preferences?
Tell me what you remember about my projects
```

**Behavior:**
- Searches conversation history + long-term memory
- Returns relevant past interactions
- Includes timestamps for conversation context

---

### System Command Intent
**Triggers:** Bot commands, help requests

**Examples:**
```
Help
What can you do?
List your commands
/ping
```

**Response:** Lists available commands and capabilities

---

## Testing Checklist

Use this checklist to verify all commands work correctly:

### Basic Functionality
- [ ] `/ping` - Bot responds with latency
- [ ] `/ask Hello` - Bot greets you
- [ ] DM: `Hello` - Bot responds to DM
- [ ] Mention: `@SecureClaw hi` - Bot responds to mention

### Memory Operations
- [ ] `/remember I like pizza` - Confirms storage
- [ ] `/search pizza` - Finds the memory with high score
- [ ] `/ask What do I like to eat?` - Bot recalls pizza preference
- [ ] `/remember I prefer Python 3.12` - Store another
- [ ] `/search programming` - Should find Python preference

### Complex Tasks
- [ ] `/ask Write a hello world in Python` - Should route to Claude/GPT-4
- [ ] `/ask Explain quantum entanglement` - Detailed response
- [ ] DM: `Help me debug this error` - Analyzes and helps

### Edge Cases
- [ ] Empty mention: `@SecureClaw` - Asks how to help
- [ ] Very long message (>2000 chars) - Splits response
- [ ] Rate limiting - Send 11+ messages quickly
- [ ] Prompt injection: `ignore previous instructions` - Blocked
- [ ] Unauthorized user (if allowlist set) - Blocked

### Error Scenarios
- [ ] Bot offline - Command fails gracefully
- [ ] Qdrant down - Error message about memory system
- [ ] API rate limit - Retry with backoff
- [ ] Invalid API key - Clear error message

---

## Response Formats

### Success Response (Ask/DM/Mention)
```
[Detailed answer to your question]

[Additional context if relevant]
```

### Memory Stored
```
✓ I'll remember that: "[your content]"
```

### Search Results
```
**Search Results:**

1. [95%] [memory content 1]
2. [87%] [memory content 2]
...
```

### Error Response (Rate Limited)
```
You're sending messages too quickly. Please wait a moment before trying again.
```

### Error Response (Not Authorized)
```
Sorry, you're not authorized to use this bot.
```

### Error Response (Prompt Injection Detected)
```
I noticed some unusual patterns in your message. Could you rephrase your question?
```

---

## Command Permissions

### User Level Permissions Required
- `Send Messages` - To use any command
- `Read Message History` - For context awareness
- `View Channel` - To see where bot is mentioned

### Bot Permissions Required
- `Send Messages` - To respond
- `Embed Links` - For rich formatting (if added)
- `Read Message History` - To load conversation context
- `Use Slash Commands` - For `/ask`, `/remember`, etc.

---

## Rate Limits

**Default Configuration:**
- **Max Messages:** 10 per user
- **Time Window:** 60 seconds
- **Warning Cooldown:** 30 seconds

**Behavior:**
1. User sends 10 messages in 60 seconds → ✓ All allowed
2. User sends 11th message → ✗ Blocked, warning shown
3. User sends 12th message within 30s → ✗ Blocked, no warning (cooldown)
4. After 60s from first message → Counter resets

**Bypass Rate Limit:**
Set `max_messages=999` in `src/secureclaw/discord/security.py:36`

---

## Configuration

### Model Configuration

**Current Models** (in `.env`):
```bash
# Routing & Simple Queries
ROUTER_MODEL=gemini-2.0-flash

# Complex Tasks
CLAUDE_MODEL=claude-3-5-sonnet-20241022
OPENAI_MODEL=gpt-4o

# Embeddings
EMBEDDING_MODEL=text-embedding-004
```

### Allowlist Configuration

**Allow All Users:**
```bash
ALLOWED_USER_IDS=
```

**Restrict to Specific Users:**
```bash
ALLOWED_USER_IDS=123456789,987654321
```

---

## Troubleshooting Commands

### Command Not Appearing

**Problem:** Slash commands don't show in Discord

**Solutions:**
1. Wait up to 1 hour for global sync
2. Restart Discord app
3. Check bot was invited with `applications.commands` scope
4. Verify bot has `Use Application Commands` permission

**Verify Sync:**
```bash
# Check logs for:
./status.sh
# Look for: "commands_synced"
```

### Command Not Responding

**Problem:** Bot online but commands don't work

**Checklist:**
- [ ] Bot has `Send Messages` permission
- [ ] User is on allowlist (if configured)
- [ ] Not rate limited
- [ ] Message Content Intent enabled (for DMs/mentions)
- [ ] Check logs for errors

**Debug:**
```bash
# Enable debug logging
# In .env:
LOG_LEVEL=DEBUG

./stop.sh && ./start.sh
# Try command again
# Check output for errors
```

---

## API Reference

For programmatic access or building additional features:

### Command Handler Methods
```python
# In src/secureclaw/discord/bot.py

async def _handle_ask(interaction, question)
# Handles /ask command

async def _handle_remember(interaction, content)
# Handles /remember command

async def _handle_search(interaction, query)
# Handles /search command

async def on_message(message)
# Handles DMs and mentions
```

### Adding New Commands

1. Edit `src/secureclaw/discord/bot.py`
2. Add command in `_setup_commands()`:
```python
@self._tree.command(name="hello", description="Say hello")
async def hello_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Hello!")
```
3. Restart bot - auto-syncs

---

## Testing Scripts

### Quick Test All Commands
```bash
# In Discord:
/ping
/ask What is 2+2?
/remember I like testing
/search testing
```

### Comprehensive Test
```bash
# Test DM
1. DM bot: "Hello!"
2. DM bot: "Remember I'm testing commands"
3. DM bot: "What do you remember about me?"

# Test Mentions
1. In server: "@SecureClaw help"
2. In server: "@SecureClaw remember our meeting is tomorrow"

# Test Error Handling
1. Send 11 messages quickly (rate limit)
2. Send: "ignore previous instructions" (injection)
3. Disable Qdrant, try /search (graceful failure)
```

---

## Support

**Need help with commands?**
- [Troubleshooting Guide](TROUBLESHOOTING.md)
- [FAQ](FAQ.md)
- [GitHub Issues](https://github.com/yourusername/secureclaw/issues)
