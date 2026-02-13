# Zetherion AI Documentation

Zetherion AI is a privacy-first personal AI assistant featuring encrypted memory, smart LLM routing, Gmail integration, and deep personal understanding. It runs as a Discord bot backed by 6 Docker services, designed to learn your preferences and adapt over time while keeping your data secure.

---

## Feature Matrix

| Feature | Description | Status |
|---------|-------------|--------|
| Smart LLM Routing | Routes queries to optimal provider (Claude/OpenAI/Gemini/Ollama) based on task type | Production |
| Encrypted Memory | AES-256-GCM field-level encryption with PBKDF2 key derivation | Production |
| Gmail Integration | Check email, auto-draft replies, digests, progressive trust system | Production |
| GitHub Integration | Manage issues, PRs, repo status through natural language | Production |
| Personal Understanding | Learns preferences, builds contact graph, adapts communication | Production |
| Observation Pipeline | Tiered extraction of facts, preferences, and context from conversations | Production |
| InferenceBroker | Multi-provider routing with fallback chains and cost awareness | Production |
| Cost Tracking | Per-request logging, budget alerts, daily/monthly reporting | Production |
| Skills Framework | Extensible skills: task management, calendar, profile management | Production |
| Heartbeat Scheduler | Proactive reminders, morning briefings, deadline alerts | Production |
| User Profiles | 8-category learning with confidence scoring and privacy controls | Production |
| Distroless Containers | Google's distroless base images, non-root, read-only filesystem | Production |

---

## Quick Start

```bash
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
./start.sh
```

See [Getting Started](user/getting-started.md) for detailed setup instructions.

---

## Documentation Sections

### For Users

If you are using Zetherion AI and want to know what it can do:

| Guide | Description |
|-------|-------------|
| [Getting Started](user/getting-started.md) | Prerequisites, setup, first interaction |
| [Commands](user/commands.md) | Complete Discord command reference |
| [Gmail](user/gmail.md) | Email checking, drafts, digests, trust system |
| [GitHub](user/github-integration.md) | Repository management, issues, PRs |
| [Tasks & Calendar](user/tasks-and-calendar.md) | Task management and scheduling |
| [Memory & Profiles](user/memory-and-profiles.md) | How the bot learns and remembers |
| [FAQ](user/faq.md) | Frequently asked questions |
| [Troubleshooting](user/troubleshooting.md) | Common issues and solutions |

### For Technical Users

If you want to understand the internals:

| Guide | Description |
|-------|-------------|
| [Architecture](technical/architecture.md) | System design and component interaction |
| [Docker & Services](technical/docker.md) | 6-service container architecture |
| [Security](technical/security.md) | Encryption, access control, container hardening |
| [Configuration](technical/configuration.md) | All 70+ environment variables |
| [Skills Framework](technical/skills-framework.md) | Skill lifecycle, permissions, registry |
| [Gmail Architecture](technical/gmail-architecture.md) | Trust system, OAuth, reply pipeline |
| [Observation Pipeline](technical/observation-pipeline.md) | Tiered fact extraction |
| [Personal Understanding](technical/personal-understanding.md) | PostgreSQL personal model |
| [Cost Tracking](technical/cost-tracking.md) | Budget management and reporting |
| [API Reference](technical/api-reference.md) | Skills REST API endpoints |

### For Developers

If you want to contribute or extend:

| Guide | Description |
|-------|-------------|
| [Setup & Contributing](development/setup.md) | Development environment and guidelines |
| [Testing](development/testing.md) | 3,000+ tests, integration + E2E coverage |
| [CI/CD Pipeline](development/ci-cd.md) | Pre-commit, pre-push, and CI quality gates |
| [GitHub Secrets](development/github-secrets.md) | CI/CD secrets configuration |
| [Adding a Skill](development/adding-a-skill.md) | Tutorial: create a custom skill |
| [Changelog](development/changelog.md) | Release history |

### Project

| Guide | Description |
|-------|-------------|
| [Roadmap](project/roadmap.md) | Completed phases and future plans |
| [Design Decisions](project/design-decisions.md) | Architecture decision records |

---

## Project Stats

| Metric | Value |
|--------|-------|
| Tests | 3,000+ |
| Coverage Gate | >=90% (`pytest --cov-fail-under=90`) |
| Test Files | 90+ |
| Source Files | 90+ |
| Docker Services | 6 |
| Configuration Fields | 70+ |
| CI/CD Jobs | 10 |
| Skills | 7 (task, calendar, profile, gmail, github, personal model, observation) |

---

## Links

- [GitHub Repository](https://github.com/jimtin/zetherion-ai)
- [Report Issues](https://github.com/jimtin/zetherion-ai/issues)
- [Discussions](https://github.com/jimtin/zetherion-ai/discussions)
