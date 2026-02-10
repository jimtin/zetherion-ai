# Getting Started

This guide walks you through everything you need to install, configure, and run
Zetherion AI as your personal Discord assistant. Most users are up and running
in under ten minutes.

---

## What You Need

| Requirement | Details |
|---|---|
| Docker Desktop 4.0+ | Required. Zetherion AI runs as 6 Docker services. |
| Discord bot token | Required. Free to create in the Discord Developer Portal. |
| Gemini API key | Required. Free tier from Google AI Studio (1,500 requests/day). |
| Anthropic API key | Optional. Enables Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`). |
| OpenAI API key | Optional. Enables GPT-5.2. |
| Python 3.12+ | Required on the host for the setup script. |
| Minimum hardware | 8 GB RAM, 20 GB free disk space. |
| Recommended hardware | 16 GB RAM, 30 GB SSD. |

---

## Step 1: Get Your API Keys

### Discord Bot Token

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and give it a name (e.g., "Zetherion AI").
3. Navigate to the **Bot** tab in the left sidebar.
4. Click **Reset Token** and copy the token. Store it somewhere safe -- you will
   not be able to view it again.
5. Scroll down to **Privileged Gateway Intents** and enable **Message Content
   Intent**.
6. Navigate to **OAuth2 > URL Generator**.
7. Under **Scopes**, select `bot` and `applications.commands`.
8. Under **Bot Permissions**, select at minimum: Send Messages, Embed Links,
   Read Message History, and Use Slash Commands.
9. Copy the generated URL. You will use it in Step 3 to invite the bot to your
   server.

### Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Click **Create API key**.
3. Copy the key. The free tier provides 1,500 requests per day, which is more
   than enough for personal use.

### Optional API Keys

**Anthropic (Claude Sonnet 4.5)**

1. Visit the [Anthropic Console](https://console.anthropic.com/).
2. Navigate to **API Keys** and create a new key.
3. Add credit to your account. Claude Sonnet 4.5 is a paid model.

**OpenAI (GPT-5.2)**

1. Visit the [OpenAI Platform](https://platform.openai.com/api-keys).
2. Create a new secret key.
3. Ensure your account has billing enabled.

---

## Step 2: Install and Run

### macOS / Linux

```bash
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
chmod +x start.sh
./start.sh
```

### Windows

```powershell
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
.\start.ps1
```

### What the Script Does

The setup script handles the entire installation process:

1. **Checks prerequisites** -- verifies that Python 3.12+ and Docker Desktop
   are installed and running.
2. **Guides you through interactive configuration** -- prompts for your Discord
   bot token, Gemini API key, and optional keys for Anthropic and OpenAI.
3. **Asks you to choose a router backend**:
   - **Gemini** (cloud) -- fast setup, roughly 3 minutes. Uses `gemini-2.5-flash`
     for intent routing. No extra hardware needed.
   - **Ollama** (local) -- private, roughly 9 minutes. Downloads and runs
     `llama3.2:1b` for routing and `llama3.1:8b` for local generation. All
     inference stays on your machine.
4. **Builds and starts all 6 Docker services**: `bot`, `skills`, `qdrant`,
   `postgres`, `ollama`, and `ollama-router`.
5. **Downloads Ollama models** if you selected the local backend.

You do not need to edit any configuration files manually. The script generates
everything for you.

---

## Step 3: Invite Your Bot

1. Return to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Select your application and go to **OAuth2 > URL Generator**.
3. Under **Scopes**, check `bot` and `applications.commands`.
4. Under **Bot Permissions**, select:
   - Send Messages
   - Embed Links
   - Attach Files
   - Read Message History
   - Use Slash Commands
   - Add Reactions
5. Copy the generated invite URL and open it in your browser.
6. Select the Discord server you want to add the bot to and click **Authorize**.

---

## Step 4: Verify Everything Works

### Check Service Health

Run the status script to confirm all services are up:

```bash
# macOS / Linux
./status.sh

# Windows
.\status.ps1
```

You should see all 6 services reported as **healthy**:

```
bot            healthy
skills         healthy
qdrant         healthy
postgres       healthy
ollama         healthy
ollama-router  healthy
```

### Test in Discord

In any channel where the bot has access, send:

```
@Zetherion AI hello
```

The bot should respond within a few seconds.

### Check Qdrant Dashboard

Open [http://localhost:6333/dashboard](http://localhost:6333/dashboard) in your
browser to verify the vector database is running and accessible.

---

## Hardware Recommendations

| Backend | RAM | CPU | GPU | Setup Time | Notes |
|---|---|---|---|---|---|
| Gemini (cloud) | 8 GB | Any | Not needed | ~3 min | Simplest option. Requires internet. |
| Ollama (`llama3.1:8b`) | 12-16 GB | 4+ cores | Not needed | ~9 min | Fully local. Private inference. |
| Ollama with GPU | 16 GB+ | 4+ cores | NVIDIA or Apple Silicon | ~9 min | Fastest local inference. |

**Notes on Ollama with GPU:**

- On macOS with Apple Silicon (M1/M2/M3/M4), Ollama uses the unified memory
  architecture automatically. No extra configuration is needed.
- On Linux with an NVIDIA GPU, ensure you have the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed so Docker can access the GPU.
- On Windows with an NVIDIA GPU, use WSL2 with the NVIDIA Container Toolkit.

---

## Managing Your Bot

| Action | Command |
|---|---|
| Start | `./start.sh` (or `.\start.ps1` on Windows) |
| Stop | `./stop.sh` (or `.\stop.ps1` on Windows) |
| Status | `./status.sh` (or `.\status.ps1` on Windows) |
| View logs | `docker-compose logs -f zetherion-ai-bot` |
| View all logs | `docker-compose logs -f` |
| Update | `git pull && ./stop.sh && ./start.sh --force-rebuild` |

### Troubleshooting

If a service fails to start:

1. Run `./status.sh` to identify which service is unhealthy.
2. Check the logs for that service: `docker-compose logs <service-name>`.
3. Ensure Docker Desktop is running and has enough allocated memory.
4. If you changed API keys, re-run `./start.sh` to regenerate the configuration.

---

## Next Steps

Now that Zetherion AI is running, explore what it can do:

- [Commands](commands.md) -- full list of available commands and slash commands.
- [Tasks and Calendar](tasks-and-calendar.md) -- manage tasks and check your
  schedule through natural language.
- [Memory and Profiles](memory-and-profiles.md) -- how the bot learns your
  preferences over time.
- [Gmail Integration](gmail.md) -- connect your Gmail account for
  email summaries and drafting.
- [GitHub Integration](github-integration.md) -- monitor repositories, review
  PRs, and track issues.
- [Configuration](../technical/configuration.md) -- advanced configuration
  options and environment variables.
