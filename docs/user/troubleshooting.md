# Troubleshooting

This guide covers common issues and their solutions when running Zetherion AI. If your issue is not listed here, see the [Getting Help](#getting-help) section at the bottom.

---

## Discord Issues

### Bot Not Responding

**Symptoms:** The bot is online but does not respond to mentions or DMs.

**Check the following in order:**

1. **Message Content Intent:** Ensure the Message Content Intent is enabled in the Discord Developer Portal under Bot > Privileged Gateway Intents.

2. **Bot Permissions:** The bot requires these channel permissions:
   - Send Messages
   - Read Messages / View Channels
   - Embed Links

3. **User Allowlist:** If `ALLOWED_USER_IDS` is set in `.env`, the user must be included. Leave it empty to allow all users.

4. **Rate Limits:** The default rate limit is 10 messages per 60 seconds per user. If you are hitting this limit, wait before sending more messages.

### Slash Commands Not Appearing

**Symptoms:** Cannot see `/ask`, `/remember`, `/search`, or other commands.

1. **Wait for sync:** Global command sync can take up to 1 hour. Be patient after first startup.

2. **Reinvite with correct scope:** The bot must be invited with the `applications.commands` scope. Go to OAuth2 > URL Generator in the Discord Developer Portal, select both `bot` and `applications.commands`, and use the new invite URL.

3. **Restart Discord:** Close and reopen the Discord application to force a command cache refresh.

### PrivilegedIntentsRequired Error

**Full error:**
```
discord.errors.PrivilegedIntentsRequired: Shard ID None is requesting privileged intents
that have not been explicitly enabled in the developer portal.
```

**Solution:**
1. Go to https://discord.com/developers/applications
2. Select your bot application.
3. Go to the **Bot** tab.
4. Scroll to **Privileged Gateway Intents**.
5. Enable **MESSAGE CONTENT INTENT** (toggle it ON).
6. Click **Save Changes**.
7. Restart the bot: `./stop.sh && ./start.sh`

---

## Configuration Issues

### Invalid ALLOWED_USER_IDS

**Error:**
```
pydantic_settings.sources.SettingsError: error parsing value for field "allowed_user_ids"
```

The correct format is comma-separated IDs with no spaces and no brackets:

```
ALLOWED_USER_IDS=123456789,987654321
```

Common mistakes:
```
# WRONG - spaces after comma:
ALLOWED_USER_IDS=123456789, 987654321

# WRONG - JSON format:
ALLOWED_USER_IDS=[123456789]

# CORRECT - no spaces, no brackets:
ALLOWED_USER_IDS=123456789,987654321
```

To get your Discord user ID, enable Developer Mode in Discord (Settings > Advanced > Developer Mode), then right-click your username and select "Copy User ID".

### Missing Environment Variables

**Required variables:**
- `DISCORD_TOKEN` -- your Discord bot token
- `GEMINI_API_KEY` -- your Google Gemini API key

**Optional variables:**
- `ANTHROPIC_API_KEY` -- for Claude Sonnet 4.5 (complex tasks)
- `OPENAI_API_KEY` -- for GPT-5.2 (alternative complex tasks)
- `GITHUB_TOKEN` -- for GitHub integration
- `ALLOWED_USER_IDS` -- defaults to allow all users
- `QDRANT_HOST` -- defaults to "qdrant" in Docker Compose
- `QDRANT_PORT` -- defaults to 6333

If your `.env` file is missing, create one from the template:
```bash
cp .env.example .env
```

---

## Docker Issues

### Docker Not Running

**Error:**
```
Cannot connect to the Docker daemon
```

The `./start.sh` script auto-detects whether Docker is running and will attempt to launch Docker Desktop automatically. If automatic launch fails:

1. Open Docker Desktop manually.
2. Wait for the green icon in the menu bar (indicating the daemon is ready).
3. Verify with `docker ps`.
4. Retry: `./start.sh`

### Port Already in Use

**Error:**
```
Error starting userland proxy: listen tcp 0.0.0.0:6333: bind: address already in use
```

Identify what is using the port:
```bash
lsof -i :6333   # Qdrant default port
lsof -i :11434  # Ollama default port
lsof -i :5432   # PostgreSQL default port
```

Kill the conflicting process or change the port in your `.env` file.

### Out of Memory

Docker Desktop may not have enough memory allocated for all 6 services (bot, skills, qdrant, postgres, ollama, ollama-router). Increase the allocation in Docker Desktop under Settings > Resources > Advanced.

Recommended memory allocation:
- **Gemini backend:** 4GB minimum (no local inference)
- **Ollama with Llama 3.1 8B:** 8GB minimum
- **Ollama with larger models:** 12-24GB

The `./start.sh` script handles memory detection and will prompt you to increase allocation if needed.

---

## Qdrant Issues

### Connection Refused

**Error:**
```
ConnectionRefusedError: [Errno 61] Connection refused
```

1. **Check the container is running:**
   ```bash
   docker ps | grep qdrant
   ```

2. **Verify host setting:** Use `QDRANT_HOST=localhost` for local development or `QDRANT_HOST=qdrant` when running inside Docker Compose.

3. **Health check:**
   ```bash
   curl http://localhost:6333/healthz
   ```
   Should return: `healthy`

4. **Restart the container:**
   ```bash
   docker restart zetherion-ai-qdrant
   ```

### Data Persistence

If memories disappear after restart, check that volume mounts are configured correctly. Data is stored in the `qdrant_storage/` directory. Verify the mount:
```bash
docker inspect zetherion-ai-qdrant | grep -A 5 Mounts
```

---

## PostgreSQL Issues

### Connection Refused

**Error:**
```
psycopg2.OperationalError: could not connect to server: Connection refused
```

1. **Check the container is running:**
   ```bash
   docker ps | grep postgres
   ```

2. **Verify settings in `.env`:**
   - `POSTGRES_HOST` -- use `localhost` for local development, `postgres` for Docker Compose
   - `POSTGRES_PORT` -- default is 5432
   - `POSTGRES_DB` -- the database name

3. **Check container logs:**
   ```bash
   docker-compose logs postgres
   ```

4. **Test the connection:**
   ```bash
   docker exec zetherion-ai-postgres pg_isready
   ```

### Migration Issues

After updates, the database schema may need to be migrated. Check the logs for migration errors:
```bash
docker-compose logs zetherion-ai-bot | grep -i migration
```

If migrations fail, check that the PostgreSQL container is fully initialized before the bot starts. The health check in Docker Compose should handle this, but on slow systems you may need to restart the bot after PostgreSQL is ready.

---

## Ollama Issues

### Model Download Fails

**Symptoms:** Ollama cannot pull the required model during startup.

1. **Check internet connectivity and disk space.** Models require 5-10GB of free disk space.

2. **Manually pull the model:**
   ```bash
   docker exec zetherion-ai-ollama ollama pull llama3.1:8b
   ```

3. **Fallback to Gemini:** If local models are not working, switch to the cloud backend:
   ```
   ROUTER_BACKEND=gemini
   ```

### Slow Responses

If Ollama is taking 30+ seconds to respond:

1. **Check Docker memory.** If the model exceeds available memory, it will swap to disk and become extremely slow.
   ```bash
   docker stats zetherion-ai-ollama
   ```

2. **Try a smaller model.** Llama 3.2 1B is used for routing and should be fast. If generation with Llama 3.1 8B is too slow, consider switching to Gemini for generation.

3. **GPU acceleration** is automatic on NVIDIA GPUs (with Docker GPU support) and Apple Silicon Macs (via Metal).

### Out of Memory

**Error:**
```
Error: llama runner process has terminated: signal: killed
```

The model requires more memory than Docker has allocated. The `./start.sh` script detects this and offers to increase allocation automatically. To fix manually, increase Docker Desktop memory under Settings > Resources > Advanced.

---

## Gmail Issues

### "Gmail is not configured"

Gmail integration requires OAuth credentials to be set up. This is a separate configuration step beyond the basic bot setup. Refer to the Gmail integration documentation for instructions on obtaining and configuring OAuth credentials.

### OAuth Authorization Fails

- Verify that your OAuth client credentials are valid and not expired.
- Ensure the redirect URI in your Google Cloud Console matches the URI configured in Zetherion AI.
- Try revoking access in your Google Account security settings and re-authorizing.

### No Accounts Connected

Connect a Gmail account by sending:
```
@Zetherion AI connect gmail
```
The bot will provide an authorization link. Follow the link to grant access.

### Sync Issues

Check the status of your connected account:
```
@Zetherion AI gmail status
```

If the OAuth token has expired, you may need to reconnect. The bot will prompt you if re-authorization is needed.

---

## GitHub Issues

### "GitHub client not initialized"

The GitHub integration requires a personal access token. Set it in your `.env` file:
```
GITHUB_TOKEN=ghp_your_token_here
```

### Authentication Failed

Your token may be expired or missing required scopes. Generate a new token at GitHub > Settings > Developer settings > Personal access tokens. Ensure it has the `repo` scope at minimum.

### "No repository specified"

Set a default repository in your `.env` file:
```
GITHUB_DEFAULT_REPO=owner/repo
```

Alternatively, specify the repository directly in your command:
```
@Zetherion AI list issues in owner/repo
```

---

## API Key Issues

### Invalid Discord Token

**Error:**
```
discord.errors.LoginFailure: Improper token has been passed
```

Regenerate your token at the Discord Developer Portal > Bot > Reset Token. Copy the new token immediately and update your `.env` file.

### Invalid Gemini Key

**Error:**
```
google.api_core.exceptions.PermissionDenied: 403 API key not valid
```

Verify your key at [Google AI Studio](https://aistudio.google.com/app/apikey). Ensure the Gemini API is enabled for your project.

### Rate Limiting (429 Errors)

**Error:**
```
429 Too Many Requests
```

The bot has automatic retry with exponential backoff. Wait 1-2 minutes before trying again. Check your API dashboards for quota limits:

- Anthropic: https://console.anthropic.com/
- OpenAI: https://platform.openai.com/usage
- Google: https://aistudio.google.com/app/apikey

---

## Performance Issues

### Slow Responses

Response times vary by provider:

- **Simple queries** route to Gemini 2.5 Flash (typically 1-3 seconds).
- **Complex queries** route to Claude Sonnet 4.5 or GPT-5.2 (typically 3-10 seconds).

To improve response times:
- Reduce the context window: set `CONTEXT_WINDOW_SIZE=5` in `.env`.
- Reduce memory search results: set `MEMORY_SEARCH_LIMIT=3` in `.env`.
- Ensure Qdrant is running on an SSD for fast vector lookups.

### High Memory Usage

Check container resource consumption:
```bash
docker stats
```

If Qdrant is using excessive memory, you can limit it with the `--memory` flag when running the container manually. For Docker Compose deployments, set memory limits in the `docker-compose.yml` file.

---

## Observation Pipeline Issues

### Observations Not Being Stored

If the bot is not learning from conversations or updating user profiles:

1. **Check that PostgreSQL is running** -- the observation pipeline stores data in PostgreSQL.
   ```bash
   docker ps | grep postgres
   ```

2. **Check bot logs for errors:**
   ```bash
   docker-compose logs -f zetherion-ai-bot | grep -i observation
   ```

3. **Verify the pipeline is enabled** in your configuration. The observation pipeline runs asynchronously after each conversation turn.

### Profile Not Updating

The profile system uses confidence scoring and requires multiple observations before updating a category. A single mention may not be enough to create a profile entry. Continue interacting naturally and the profile will populate over time.

---

## Debug Information

Use these commands to gather diagnostic information:

```bash
./status.sh                              # Overall system status
docker-compose logs -f zetherion-ai-bot  # Bot logs (live follow)
docker stats                             # Container resource usage
curl http://localhost:6333/healthz       # Qdrant health check
docker exec zetherion-ai-postgres pg_isready  # PostgreSQL health check
```

To enable verbose logging, set the log level in your `.env` file:
```
LOG_LEVEL=DEBUG
```

Then restart the bot: `./stop.sh && ./start.sh`

---

## Getting Help

If your issue is not covered in this guide:

1. Review the [FAQ](faq.md) for common questions.
2. Search [GitHub Issues](https://github.com/JamesHinton/zetherion-ai/issues) for existing reports.
3. Create a new issue and include:
   - Your OS version and Docker version
   - Output of `./status.sh`
   - Relevant log output (`docker-compose logs zetherion-ai-bot --tail 50`)
   - Steps to reproduce the issue
   - What you have already tried
