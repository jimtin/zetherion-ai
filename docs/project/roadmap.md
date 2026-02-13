# Roadmap

Current status and future plans for Zetherion AI.

## Completed Phases

### Phase 1-4: Foundation (Complete)

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Core agent with Discord as first input interface and dual LLM backends (Gemini + Ollama) | Complete |
| 2 | Message routing with intent classification | Complete |
| 3 | Qdrant vector memory with semantic search | Complete |
| 4 | Security controls (rate limiting, allowlist, prompt injection) | Complete |

### Phase 5: Core Intelligence (Complete)

| Sub-Phase | Feature | Status |
|-----------|---------|--------|
| 5A | AES-256-GCM encryption layer | Complete |
| 5B | InferenceBroker multi-provider routing | Complete |
| 5B.1 | Model registry and cost tracking | Complete |
| 5C | User profile system with tiered inference | Complete |
| 5C.1 | Employment profile and trust levels | Complete |
| 5D | Skills framework with permissions | Complete |
| 5E | Built-in skills (tasks, calendar, profile) | Complete |
| 5F | Heartbeat scheduler | Complete |
| 5G | Router enhancement for skill intents | Complete |

### Phase 6: Docker Hardening (Complete)

- Distroless base images for bot and skills containers
- Read-only root filesystem, no-new-privileges on all containers
- Resource limits (CPU and memory) for all services
- Dual Ollama architecture (separate router and generation containers)
- Health checks on all 6 services

### Phase 7: GitHub Integration (Complete)

- GitHub skill with 18 intents for repository management
- Issue and PR management (list, view, create, close, merge)
- Three autonomy levels with safety-first defaults

### Phase 8: Gmail Integration (Complete)

- 12-file Gmail module with full email management
- Two-dimensional progressive trust system (per-contact + per-type)
- OAuth account management with encrypted token storage
- Reply draft pipeline, digest generation, email analytics
- Observation pipeline for implicit knowledge extraction

### Phase 9: Personal Understanding (Complete)

- PostgreSQL-backed personal model (profiles, contacts, policies, learnings)
- Communication style adaptation (formality, verbosity, directness, proactivity)
- Contact graph with relationship tracking
- Policy system with configurable autonomy modes

## Current State

| Metric | Value |
|--------|-------|
| Source files | 90+ Python files |
| Test files | 90+ |
| Total tests | 3,000+ |
| Coverage gate | >=90% |
| Docker services | 6 |
| Skills | 6 built-in (tasks, calendar, profile, gmail, github, personal) |

## Future Directions

The following areas are under consideration for future development. These are ideas, not commitments -- priorities may shift based on usage patterns and community feedback.

### Voice Integration

- Discord voice channel support
- Speech-to-text and text-to-speech
- Voice-triggered commands and responses

### Multi-Tenant API

- Public REST API for additional input sources and third-party integrations
- Multi-user authentication and authorization
- API rate limiting and usage tracking per tenant

### Enhanced Observation

- Deeper behavioral pattern recognition
- Cross-platform activity correlation
- Proactive insight generation based on observed patterns

### Additional Integrations

- Slack bot adapter
- Telegram bot adapter
- Microsoft Teams integration
- Jira/Linear issue tracking
- Notion/Confluence document management

### Advanced Memory

- Hierarchical memory with forgetting curves
- Cross-user knowledge sharing (opt-in)
- Memory compression and summarization
- Temporal awareness (time-sensitive memories)

### Improved Routing

- Learning-based router that adapts to query patterns
- Cost-aware routing with automatic budget optimization
- Latency-aware provider selection

### Self-Improvement

- Automated feedback collection from user interactions
- Response quality scoring
- A/B testing of different response strategies

## Contributing Ideas

Have a feature request or idea? The best ways to contribute:

1. Open a [GitHub Issue](https://github.com/jimtin/zetherion-ai/issues) with a feature proposal
2. Start a [GitHub Discussion](https://github.com/jimtin/zetherion-ai/discussions) for broader ideas
3. Submit a PR with an implementation (see [Adding a Skill](../development/adding-a-skill.md) for extending capabilities)

---

**Last Updated:** 2026-02-08
