# Frequently Asked Questions

## General

### What is Zetherion AI?

Zetherion AI is a secure, intelligent Discord bot that serves as a personal AI assistant. It features multi-provider LLM routing through its InferenceBroker, encrypted vector-based memory, and integrations with Gmail, GitHub, and calendar services. It runs entirely on your own infrastructure, giving you full control over your data.

### Is it free?

The bot itself is open source and free to use. The Gemini API has a generous free tier that is sufficient for personal use. Claude and GPT are optional providers for complex tasks and require paid API keys. Discord bot hosting is free.

### Does it store my data?

Yes, all data is stored locally on your machine. Qdrant is used for vector storage (conversation history, semantic memories), and PostgreSQL is used for structured data (user profiles, tasks, integrations). If encryption is enabled, all sensitive fields are encrypted with AES-256-GCM. Nothing is sent to third parties beyond the LLM API calls themselves.

### Can multiple people use it?

Yes. Set `ALLOWED_USER_IDS` in your `.env` file with a comma-separated list of Discord user IDs. Leave it empty to allow all users. Each user gets their own memory space and profile.

---

## Setup

### What hardware do I need?

Minimum requirements depend on your chosen backend:

- **Gemini backend (cloud routing):** 8GB RAM, 4GB free disk space
- **Ollama backend (local inference):** 12-16GB RAM, 10GB+ free disk space for model weights

See the [Getting Started guide](getting-started.md) for detailed hardware recommendations.

### Can I run it on Windows?

Yes. Use `start.ps1` in PowerShell. Docker Desktop for Windows is required. WSL2 is not needed since the PowerShell script handles everything natively.

### Do I need all API keys?

No. Only two keys are required:

- **Discord Token** (required)
- **Gemini API Key** (required)

Claude (`ANTHROPIC_API_KEY`) and GPT (`OPENAI_API_KEY`) are optional. Without them, all queries are handled by Gemini, which is still very capable for most tasks.

### Can I run it on a Raspberry Pi?

It is possible with the Gemini backend, since all heavy inference is done in the cloud. However, it is not recommended for the Ollama backend due to memory and CPU constraints. A Raspberry Pi 4 with 8GB RAM can work for Gemini-only mode.

---

## Features

### What LLM models does it use?

Zetherion AI uses multiple models, each selected for its strengths:

| Model | Role |
|---|---|
| Gemini 2.5 Flash | Routing, simple queries, embeddings |
| Claude Sonnet 4.5 | Complex reasoning, code analysis, creative tasks |
| GPT-5.2 | Alternative for complex tasks |
| Llama 3.2 1B | Local router (Ollama backend) |
| Llama 3.1 8B | Local generation (Ollama backend) |

Embeddings use Gemini text-embedding-004 (768 dimensions).

### How does routing work?

The InferenceBroker classifies each query by complexity and routes it to the optimal provider:

1. User sends a message.
2. The router (Gemini 2.5 Flash or Llama 3.2 1B) analyzes intent and complexity.
3. Simple queries (greetings, factual questions) are handled by the router model directly.
4. Complex queries (code generation, analysis, multi-step reasoning) are routed to Claude Sonnet 4.5 or GPT-5.2.

This approach keeps costs low while ensuring quality for tasks that need it.

### Can it read my email?

Yes, with Gmail integration via OAuth. The progressive trust system ensures the bot only accesses your email with explicit authorization. You can connect your account, search emails, and get summaries. See the Gmail integration documentation for setup details.

### Can it manage GitHub?

Yes. With a GitHub personal access token, Zetherion AI can manage issues, pull requests, workflows, and labels. Autonomy levels are configurable so you control how much the bot can do independently.

### Does it learn about me?

Yes. The profile system tracks information across 8 categories with confidence scoring. You have full privacy controls over what is stored, and you can review, edit, or delete profile information at any time.

### Can I use different models?

Yes. Configure models in your `.env` file:

```
CLAUDE_MODEL=claude-sonnet-4-5-20250514
OPENAI_MODEL=gpt-5.2
ROUTER_MODEL=gemini-2.5-flash
```

See `src/zetherion_ai/config.py` for all available model options.

### How much does it cost to run?

| Tier | Monthly Cost | Details |
|---|---|---|
| Free (Gemini only) | $0/month | Handles most personal use comfortably |
| With Claude | ~$5-20/month | For personal use with complex task routing |
| With GPT | ~$10-30/month | Alternative to Claude for complex tasks |

You can reduce costs by using Gemini-only mode, adjusting routing thresholds, or reducing context window sizes.

---

## Privacy and Security

### Is my data encrypted?

Yes. Zetherion AI uses AES-256-GCM field-level encryption with PBKDF2 key derivation. Sensitive fields in both Qdrant and PostgreSQL are encrypted at rest. Enable encryption by setting the encryption key in your `.env` file.

### Are API keys safe in the .env file?

Yes, provided you follow basic precautions:

- `.env` is included in `.gitignore` by default and will not be committed to Git.
- Restrict file permissions: `chmod 600 .env`
- Never share the `.env` file or post its contents anywhere.

### What about prompt injection?

Zetherion AI has built-in prompt injection detection that includes regex patterns for common injection techniques, Unicode obfuscation detection, and role-play marker detection. Suspicious messages are automatically rejected before reaching the LLM.

### Can I delete all my data?

Yes. Send `@Zetherion AI delete all my data` in Discord. The bot will ask for confirmation before permanently removing all your stored memories, profile data, and conversation history.

### Can I export my data?

Yes. Send `@Zetherion AI export my data` in Discord. The bot will compile and send you a complete export of all your stored data, supporting GDPR compliance requirements.

---

## Troubleshooting

### The bot is not responding

Common causes include:

- Message Content Intent not enabled in the Discord Developer Portal.
- User not on the allowlist (check `ALLOWED_USER_IDS`).
- Bot missing permissions (Send Messages, Read Messages, Embed Links).
- Rate limits exceeded (default is 10 messages per 60 seconds).

See the [Troubleshooting guide](troubleshooting.md) for step-by-step solutions.

### Slash commands are not appearing

Slash commands can take up to 1 hour to sync globally. Try restarting Discord, or reinvite the bot with the `applications.commands` scope. See [Troubleshooting - Slash Commands](troubleshooting.md#slash-commands-not-appearing) for details.

### API costs are too high

To reduce costs:

1. Use Gemini-only mode (remove Claude/OpenAI API keys).
2. Set budget limits in your API provider dashboards.
3. Reduce the context window size (`CONTEXT_WINDOW_SIZE` in `.env`).
4. Restrict usage with `ALLOWED_USER_IDS`.

For detailed troubleshooting, see the [Troubleshooting guide](troubleshooting.md).

---

## Still Have Questions?

1. Check the [Troubleshooting guide](troubleshooting.md).
2. Search [GitHub Issues](https://github.com/JamesHinton/zetherion-ai/issues).
3. Create a new issue with the `[Question]` tag.
