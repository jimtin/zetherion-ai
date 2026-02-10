# Zetherion AI

[![CI Pipeline](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml)
[![Test Coverage](https://img.shields.io/badge/coverage-93%25+-brightgreen)](https://github.com/jimtin/zetherion-ai/actions)
[![Tests](https://img.shields.io/badge/tests-3000+-blue)](https://github.com/jimtin/zetherion-ai/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A privacy-first personal AI assistant that learns how you work.**

---

## What Is This?

Zetherion AI is a Discord bot that acts as your personal AI assistant. It intelligently routes your queries across multiple LLM providers -- simple questions go to free-tier Gemini, complex reasoning tasks are handled by Claude or GPT, and privacy-sensitive operations stay on your machine via Ollama. Every piece of memory is encrypted with AES-256-GCM before it touches a database.

Over time, Zetherion learns your preferences, communication style, and workflows. It manages your Gmail with progressive autonomy -- starting with read-only access and gradually earning trust to draft and send replies on your behalf. It tracks your GitHub repositories, manages tasks, and recalls past conversations with precision.

Everything runs on your own infrastructure. Six Docker services, zero cloud dependencies for your personal data, and 3,000+ tests across 89 test files ensuring it all works reliably.

---

## Key Features

| Feature | Description |
| --- | --- |
| **Smart LLM Routing** | Routes queries to the optimal provider based on task complexity -- Gemini 2.5 Flash for simple tasks, Claude Sonnet 4.5 or GPT-5.2 for deep reasoning |
| **Gmail Integration** | Check email, search threads, and auto-draft replies with a progressive trust system that expands autonomy over time |
| **Encrypted Memory** | AES-256-GCM field-level encryption with PBKDF2 key derivation -- your memories are unreadable even if the database is compromised |
| **GitHub Integration** | Manage issues, pull requests, and repository status directly from chat |
| **Personal Understanding** | Learns your preferences, goals, and communication style to deliver increasingly personalized responses |
| **Observation Pipeline** | Automatically extracts facts and context from conversations without explicit commands |
| **Cost Tracking** | Per-request cost logging across all providers with configurable budget alerts |
| **Skills Framework** | Extensible architecture for task management, calendar events, and user profiles |
| **Security-First Design** | Distroless containers, prompt injection defense, rate limiting, and no-new-privileges enforcement |
| **3,000+ Tests** | 93%+ code coverage across 89 test files and 91 source modules -- tested in CI on every commit |

---

## Quick Start

```bash
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
./start.sh      # Mac/Linux  |  .\start.ps1  # Windows
```

The startup script walks you through configuration, pulls Docker images, and launches all six services. See the [full getting-started guide](docs/user/getting-started.md) for detailed setup instructions.

---

## Example Interactions

**Coding question** -- automatically routed to Claude for deep reasoning:

```text
You:  How do I implement a thread-safe singleton in Python?
Zeth: Here's a thread-safe singleton using a metaclass with a lock...
      [detailed explanation with code examples]
```

**Email management** -- check and act on your inbox from Discord:

```text
You:  Check my email
Zeth: You have 3 new emails:
      1. [GitHub] PR #187 approved by @sarah -- ready to merge
      2. [AWS] Monthly billing summary -- $42.17
      3. [Team] Meeting moved to Thursday 2pm
      Want me to draft a reply to any of these?
```

**Task tracking** -- natural language task management:

```text
You:  Add task: Review PR #42 before end of day
Zeth: Task created: "Review PR #42" with deadline today at 5:00 PM.
      I'll remind you at 4:00 PM if it's still open.
```

**Memory recall** -- Zetherion remembers your past conversations:

```text
You:  What did we discuss about Python testing last week?
Zeth: Last Tuesday you asked about pytest fixtures vs setup methods.
      Key points: you preferred fixtures for readability, and we
      covered parameterized tests for your API validation layer.
```

---

## Architecture at a Glance

```text
Discord / Slack
      |
      v
+--[ Bot Gateway ]--+
|                    |
|   Security Layer   |-----> Rate Limiting / Prompt Injection Defense
|                    |
|   Agent Core       |-----> LLM Providers
|                    |       (Claude / GPT / Gemini / Ollama)
+--------+-+--------+
         | |
    +----+ +----+
    |           |
    v           v
 Skills      Memory
 (Gmail,     (Qdrant + PostgreSQL)
  GitHub,    AES-256-GCM encrypted
  Tasks,
  Calendar)
```

**Six Docker services** orchestrate the full stack:

| Service | Role |
| --- | --- |
| `bot` | Core application -- Discord gateway, agent logic, security |
| `skills` | Gmail, GitHub, task management, calendar integrations |
| `qdrant` | Vector database for semantic memory search |
| `postgres` | Relational storage for user data, tasks, and audit logs |
| `ollama` | Local LLM inference for privacy-sensitive operations |
| `ollama-router` | Llama 3.2 1B router that classifies queries before dispatch |

Local inference uses Llama 3.2 1B for fast routing decisions and Llama 3.1 8B for generation tasks that should never leave your machine.

---

## Documentation

| Section | What You'll Find |
| --- | --- |
| [User Guide](docs/user/getting-started.md) | Getting started, commands reference, Gmail setup, GitHub integration |
| [Technical](docs/technical/architecture.md) | Architecture deep-dive, Docker configuration, security model, API reference |
| [Development](docs/development/setup.md) | Dev environment setup, running 3,000+ tests, CI/CD pipeline |
| [Project](docs/project/roadmap.md) | Roadmap, design decisions, contribution opportunities |

---

## Requirements

| Requirement | Notes |
| --- | --- |
| **Docker Desktop 4.0+** | All services run containerized -- no local Python install needed |
| **Discord bot token** | Free to create at [discord.com/developers](https://discord.com/developers/applications) |
| **Gemini API key** | Free tier covers most routing and simple queries via [Google AI Studio](https://aistudio.google.com/) |
| **Anthropic API key** | *Optional* -- enables Claude Sonnet 4.5 for complex reasoning tasks |
| **OpenAI API key** | *Optional* -- enables GPT-5.2 as an alternative reasoning provider |

Without optional API keys, Zetherion runs fully on Gemini's free tier and local Ollama models. Add paid providers when you want deeper reasoning for demanding tasks.

---

## Contributing

Contributions are welcome. The codebase maintains 93%+ coverage across 89 test files, so the bar for new contributions is straightforward -- write tests, pass CI, and follow the existing patterns. See the [development guide](docs/development/setup.md) for environment setup and testing instructions.

If you have ideas for new skills, LLM provider integrations, or security improvements, open an issue to discuss before submitting a PR.

---

## License

This project is licensed under the [MIT License](https://opensource.org/licenses/MIT).

---

*Built for people who want an AI assistant that respects their privacy.*
