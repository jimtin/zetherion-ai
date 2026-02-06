# Frequently Asked Questions (FAQ)

## General Questions

### What is Zetherion AI?
Zetherion AI is a secure, intelligent Discord bot with vector-based memory. It can remember conversations, answer questions, and assist with complex tasks using multiple AI models (Gemini, Claude, GPT-4).

### Is Zetherion AI free to use?
The bot itself is open source and free. However, you need API keys:
- **Gemini API** - Free tier available (sufficient for personal use)
- **Discord Bot** - Free
- **Claude/GPT-4** - Optional, paid tiers only

### Can I use Zetherion AI in production?
Yes, but:
- Set `ALLOWED_USER_IDS` to restrict access
- Use proper API rate limits
- Monitor costs for paid API usage
- Consider enabling additional security features

### Does Zetherion AI store my data?
Yes, Zetherion AI stores:
- **Conversation history** - In Qdrant vector database (local to your machine)
- **Long-term memories** - Things you explicitly ask it to remember
- **Nothing is sent to third parties** - All data stays on your infrastructure

Data is stored locally in the `qdrant_storage/` directory.

---

## Setup Questions

### What hardware do I need?
**Minimum:**
- 4GB RAM
- 2GB free disk space
- macOS/Linux (Windows with WSL)

**Recommended:**
- 8GB+ RAM
- 10GB free disk space
- SSD storage

### Can I run this on Windows?
Yes, but with modifications:
1. Install Docker Desktop for Windows
2. Use WSL2 (Windows Subsystem for Linux)
3. Run commands in WSL2 terminal
4. Scripts will need adjustment (or use Docker Compose instead)

### Can I run this on a Raspberry Pi?
Possibly, but not recommended:
- Requires 64-bit OS
- Minimum 4GB RAM model
- Performance will be limited
- Qdrant may struggle with large datasets

### Do I need all three API keys?
**Required:**
- Discord Token
- Gemini API Key

**Optional (but recommended):**
- Anthropic (Claude) - For better quality on complex tasks
- OpenAI (GPT-4) - Alternative to Claude

Without Claude/GPT-4, all queries use Gemini Flash (still very capable).

---

## Usage Questions

### How do I talk to the bot?
**In Discord:**
- **DM the bot directly** - Just send a message
- **Mention in server** - `@Zetherion AI your message here`
- **Slash commands** - `/ask`, `/remember`, `/search`, `/ping`

### What's the difference between `/ask` and mentioning?
None functionally - both do the same thing. Use whatever is more convenient:
- `/ask` - More explicit, good for servers
- Mentioning - More natural, like talking to a person

### Can it remember previous conversations?
Yes! Zetherion AI automatically:
- Remembers recent conversation context (last 20 messages)
- Searches relevant past conversations using vector similarity
- Recalls explicitly stored memories

### How do I make it remember something specific?
```
# Any of these work:
/remember I prefer dark mode
"Remember that I'm a Python developer"
"Note: My birthday is March 15"
```

### How do I search my memories?
```
/search preferences
/search birthday
/search python projects
```

Returns the 5 most relevant memories with similarity scores.

### Can I delete memories?
Not yet via commands, but you can:
```bash
# Delete all memories:
./stop.sh
rm -rf qdrant_storage/
./start.sh
```

Future versions will add `/forget` command.

---

## Technical Questions

### What AI models does it use?

**Routing & Simple Queries:**
- Gemini 2.0 Flash (fast, cheap, handles 90% of queries)

**Complex Tasks:**
- Claude 3.5 Sonnet (default for code, analysis, creative tasks)
- GPT-4 (alternative, configure via OPENAI_API_KEY)

**Embeddings:**
- Gemini text-embedding-004 (768 dimensions)

### How does the routing work?
1. User sends message
2. Gemini Flash analyzes intent + complexity
3. If simple (greeting, factual question) → Gemini Flash responds
4. If complex (code, analysis) → Routes to Claude/GPT-4

Threshold: 70% confidence that task is complex

### What is Qdrant?
Qdrant is a vector database that stores:
- Conversation history as semantic vectors
- Long-term memories
- Enables similarity search (find related past conversations)

Think of it like a smart search engine for your conversations.

### How much does it cost to run?

**Free Tier Usage (Gemini only):**
- ~1000 messages/day on free tier
- $0/month

**With Claude API (Recommended):**
- ~$0.003 per message (simple)
- ~$0.03 per complex task
- ~$5-20/month for personal use

**With GPT-4:**
- ~$0.01 per message
- ~$10-30/month for personal use

### How do I reduce costs?
1. Use Gemini-only (remove Claude/OpenAI keys)
2. Increase routing threshold (fewer complex tasks)
3. Reduce memory context limits
4. Set `ALLOWED_USER_IDS` to restrict usage

### Can I use different models?
Yes! Edit `.env`:
```bash
# Use Claude Haiku (cheaper, faster):
CLAUDE_MODEL=claude-3-haiku-20240307

# Use GPT-3.5 instead of GPT-4:
OPENAI_MODEL=gpt-3.5-turbo

# Use different Gemini model:
ROUTER_MODEL=gemini-1.5-flash
```

See `src/zetherion_ai/config.py` for all model options.

---

## Security Questions

### Is it safe to put API keys in .env?
Yes, if:
- `.env` is in `.gitignore` (it is by default)
- You don't commit it to GitHub
- File permissions are restricted: `chmod 600 .env`

**Never:**
- Share `.env` file
- Commit it to Git
- Post it in Discord/forums

### Should I enable the user allowlist?
**For personal use:** Not required, but recommended
```bash
ALLOWED_USER_IDS=your_discord_id
```

**For server use:** CRITICAL
```bash
ALLOWED_USER_IDS=id1,id2,id3
```

Otherwise anyone in the server can use (and rack up API costs).

### What about prompt injection attacks?
Zetherion AI has built-in protection:
- 17 regex patterns detect injection attempts
- Unicode obfuscation detection
- Excessive role-play marker detection
- Auto-rejects suspicious messages

See `src/zetherion_ai/discord/security.py` for details.

### Can someone hack my bot?
**Attack vectors:**
1. **Stolen Discord Token** - Keep token secret, rotate if exposed
2. **API Key Theft** - Protect `.env` file
3. **Prompt Injection** - Built-in protection, but not 100%
4. **Rate Limiting Abuse** - Set user allowlist + rate limits

**Best Practices:**
- Use `ALLOWED_USER_IDS` for production
- Monitor API usage dashboards
- Enable Discord 2FA
- Rotate tokens periodically

---

## Development Questions

### Can I contribute to Zetherion AI?
Yes! Contributions welcome:
1. Fork the repo
2. Create feature branch
3. Make changes + add tests
4. Submit PR with description

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### How do I run tests?
```bash
# Install dev dependencies:
pip install -r requirements-dev.txt

# Run all tests:
pytest tests/ -v

# With coverage:
pytest tests/ --cov=src/zetherion_ai --cov-report=html

# Open coverage report:
open htmlcov/index.html
```

### How do I add a new slash command?
1. Edit `src/zetherion_ai/discord/bot.py`
2. Add command in `_setup_commands()` method
3. Create handler method (e.g., `_handle_my_command`)
4. Restart bot - commands sync automatically

Example:
```python
@self._tree.command(name="hello", description="Say hello")
async def hello_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Hello!")
```

### How do I change the system prompt?
Edit `src/zetherion_ai/agent/prompts.py`:
- `CLAUDE_SYSTEM_PROMPT` - Instructions for Claude
- `OPENAI_SYSTEM_PROMPT` - Instructions for GPT-4

Restart bot after changes.

### Can I use a different vector database?
Technically yes, but requires code changes:
- Current: Qdrant (recommended, fast, easy)
- Alternatives: Pinecone, Weaviate, Milvus

You'd need to implement the same interface in `src/zetherion_ai/memory/`.

---

## Troubleshooting

### Bot is not responding
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#bot-not-responding-in-server)

### Getting API errors
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#api-key-issues)

### Qdrant connection issues
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#qdrant-connection-issues)

### Performance problems
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#performance-issues)

---

## Still Have Questions?

1. Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
2. Search [GitHub Issues](https://github.com/youruser/zetherion_ai/issues)
3. Ask in [GitHub Discussions](https://github.com/youruser/zetherion_ai/discussions)
4. Create new issue with `[Question]` tag
