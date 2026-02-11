# Zetherion AI

[![CI Pipeline](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml)
[![Test Coverage](https://img.shields.io/badge/coverage-93%25+-brightgreen)](https://github.com/jimtin/zetherion-ai/actions)
[![Tests](https://img.shields.io/badge/tests-3000+-blue)](https://github.com/jimtin/zetherion-ai/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A privacy-first personal AI assistant that learns how you work.**

---

## What Is This?

Zetherion AI is a personal AI assistant designed to accept input from any source. Discord is the first interface, but the architecture is source-agnostic -- a REST API skills layer, email sync, GitHub webhooks, and a heartbeat scheduler all operate independently of how messages arrive. Future interfaces (Slack, Telegram, Teams, or a custom frontend) plug into the same agent core.

You choose how inference runs. The system supports local models via Ollama for operations that should never leave your machine, and cloud providers (Gemini, Claude, OpenAI) for tasks where you want more capability. A smart router classifies each query and dispatches it to the provider you've configured for that task type -- you control the tradeoff between privacy, cost, and quality.

Zetherion doesn't just respond when asked. An observation pipeline passively extracts facts and preferences from your conversations without explicit commands. A heartbeat scheduler proactively surfaces task reminders, email digests, and calendar alerts on its own. A progressive trust system gradually earns autonomy over actions like drafting and sending email replies, expanding what it does automatically as you approve its judgement over time.

Every piece of memory is encrypted with AES-256-GCM before it touches a database. Everything runs on your own infrastructure. Six Docker services, zero cloud dependencies for your personal data, and 3,000+ tests across 89 test files ensuring it all works reliably.

---

## Key Features

| Feature | Description |
| --- | --- |
| **Source-Agnostic Input** | Accepts input from Discord, REST API, email sync, GitHub webhooks, and future interfaces -- not locked to any single platform |
| **User-Controlled LLM Routing** | You choose between local inference (Ollama) and cloud providers (Gemini, Claude, OpenAI) -- the router dispatches each query to the right provider based on your configuration |
| **Passive Observation** | Automatically extracts facts, preferences, and context from conversations without explicit commands via a 3-tier extraction pipeline |
| **Proactive Prompting** | Heartbeat scheduler surfaces task reminders, email digests, calendar alerts, and overdue notifications without being asked |
| **Progressive Trust** | Gmail integration starts read-only and gradually earns autonomy to draft and send replies as you approve its judgement over time |
| **Encrypted Memory** | AES-256-GCM field-level encryption with PBKDF2 key derivation -- your memories are unreadable even if the database is compromised |
| **Personal Understanding** | Learns your preferences, goals, communication style, and workflows across 8 categories with confidence scoring |
| **Gmail Integration** | Check email, search threads, and auto-draft replies with per-contact and per-reply-type trust tracking |
| **GitHub Integration** | Manage issues, pull requests, and repository status with configurable autonomy levels |
| **Skills Framework** | Extensible architecture for tasks, calendar, email, GitHub, profiles, and custom integrations |
| **Cost Tracking** | Per-request cost logging across all providers with configurable budget alerts |
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

## How It Works

Zetherion operates in three modes simultaneously:

### Active Interaction

You ask a question, and the router classifies it and dispatches to the appropriate provider and skill:

```text
You:  How do I implement a thread-safe singleton in Python?
Zeth: Here's a thread-safe singleton using a metaclass with a lock...
      [detailed explanation with code examples]
```

```text
You:  Check my email
Zeth: You have 3 new emails:
      1. [GitHub] PR #187 approved by @sarah -- ready to merge
      2. [AWS] Monthly billing summary -- $42.17
      3. [Team] Meeting moved to Thursday 2pm
      Want me to draft a reply to any of these?
```

### Passive Observation

Zetherion listens to your conversations and silently extracts facts and preferences. No commands needed -- it builds a model of who you are over time:

```text
You:  I've been using pytest a lot lately, prefer fixtures over
      setup methods. Working on the API validation layer this week.

      [Zetherion silently records: prefers pytest fixtures,
       current project involves API validation, active this week]
```

Next time you ask about testing, it already knows your preferences and context.

### Proactive Prompting

The heartbeat scheduler checks for things you should know about and surfaces them without being asked:

```text
Zeth: Heads up -- "Review PR #42" is due in 1 hour and still open.

Zeth: Morning email digest: 5 new emails overnight.
      1 from @sarah (PR feedback) looks urgent.
      Want me to draft a reply?

Zeth: You have a meeting with the platform team in 30 minutes.
```

Proactive actions respect quiet hours (default 10 PM - 7 AM) and rate limits.

---

## Architecture at a Glance

```text
Any Input Source
(Discord / REST API / Email / Webhooks / Future interfaces)
      |
      v
+--[ Agent Core ]------+
|                       |
|   Security Layer      |-----> Rate Limiting / Prompt Injection Defense
|                       |
|   Router              |-----> Intent Classification
|                       |
|   Inference Broker    |-----> LLM Providers (your choice):
|                       |       Local: Ollama (Llama 3.1 8B / 3.2 1B)
|                       |       Cloud: Gemini / Claude / OpenAI
+--------+-+-----------+
         | |
    +----+ +----+
    |           |
    v           v
 Skills      Memory
 (Gmail,     (Qdrant + PostgreSQL)
  GitHub,    AES-256-GCM encrypted
  Tasks,
  Calendar,       Observation Pipeline
  Profile)        (passive learning)
    |
    v
 Heartbeat Scheduler
 (proactive actions)
```

**Six Docker services** orchestrate the full stack:

| Service | Role |
| --- | --- |
| `bot` | Agent core -- input gateway, security, routing, inference |
| `skills` | REST API for Gmail, GitHub, tasks, calendar, profiles, heartbeat |
| `qdrant` | Vector database for semantic memory search |
| `postgres` | Relational storage for user data, profiles, trust scores, and audit logs |
| `ollama` | Local LLM inference (Llama 3.1 8B for generation, nomic-embed-text for embeddings) |
| `ollama-router` | Dedicated routing container (Llama 3.2 1B for fast query classification) |

---

## LLM Provider Configuration

You control which providers handle which tasks. The system supports mixing local and cloud inference:

| Provider | Models | Typical Use | Cost |
| --- | --- | --- | --- |
| **Ollama (local)** | Llama 3.1 8B, Llama 3.2 1B | Privacy-sensitive operations, routing | Free |
| **Gemini** | 2.5 Flash | Simple queries, classification | Free tier available |
| **Claude** | Sonnet 4.5 | Complex reasoning, code generation | Paid |
| **OpenAI** | GPT-5.2 | Alternative complex reasoning | Paid |

Without cloud API keys, Zetherion runs entirely on local Ollama models. Add cloud providers when you want more capability for specific task types.

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
| **Docker Desktop 4.0+** | All services run containerized -- no local Python install needed for running |
| **Discord bot token** | Free to create at [discord.com/developers](https://discord.com/developers/applications) -- the first supported input source |
| **Gemini API key** | *Optional* -- enables cloud inference via [Google AI Studio](https://aistudio.google.com/) (free tier available) |
| **Anthropic API key** | *Optional* -- enables Claude Sonnet 4.5 for complex reasoning tasks |
| **OpenAI API key** | *Optional* -- enables GPT-5.2 as an alternative reasoning provider |

The only hard requirement beyond Docker is a Discord bot token (as the first input interface). All LLM providers are optional -- Ollama provides fully local inference out of the box.

---

## Contributing

Contributions are welcome. The codebase maintains 93%+ coverage across 89 test files, so the bar for new contributions is straightforward -- write tests, pass CI, and follow the existing patterns. See the [development guide](docs/development/setup.md) for environment setup and testing instructions.

If you have ideas for new input interfaces, skills, LLM provider integrations, or security improvements, open an issue to discuss before submitting a PR.

---

## License

This project is licensed under the [MIT License](https://opensource.org/licenses/MIT).

---

*Built for people who want an AI assistant that respects their privacy and works on their terms.*
