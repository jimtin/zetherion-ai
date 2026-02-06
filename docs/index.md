# Zetherion AI

Secure personal AI assistant with encrypted memory, multi-provider LLM routing, and privacy-first design.

## Features

- **Encrypted Memory** - AES-256-GCM encryption for all stored data with PBKDF2 key derivation
- **Multi-Provider Routing** - Intelligent routing across Claude, OpenAI, Gemini, and Ollama
- **Vector Memory** - Long-term context using Qdrant with semantic search
- **Security-First** - Rate limiting, prompt injection detection, secrets management
- **Self-Hosted** - Run entirely on your own infrastructure with Ollama

## Quick Start

```bash
# Clone the repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start with Docker
./start.sh
```

See the [Startup Walkthrough](STARTUP_WALKTHROUGH.md) for detailed setup instructions.

## Documentation

| Section | Description |
|---------|-------------|
| [Commands](COMMANDS.md) | Discord slash commands reference |
| [Architecture](ARCHITECTURE.md) | System design and components |
| [Security](SECURITY.md) | Security controls and best practices |
| [Testing](TESTING.md) | Test suite and coverage |
| [Docker](DOCKER_ARCHITECTURE.md) | Container setup and networking |
| [CI/CD](CI_CD.md) | Continuous integration pipeline |
| [Troubleshooting](TROUBLESHOOTING.md) | Common issues and solutions |
| [FAQ](FAQ.md) | Frequently asked questions |

## Project Status

- Test Coverage: **87.58%** (255 unit + 14 integration + 4 E2E tests)
- Current Phase: 5 (Encrypted memory, InferenceBroker complete)

## Links

- [GitHub Repository](https://github.com/jimtin/zetherion-ai)
- [Report Issues](https://github.com/jimtin/zetherion-ai/issues)
- [Wiki](https://github.com/jimtin/zetherion-ai/wiki)
