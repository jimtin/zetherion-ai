# Zetherion AI

Secure personal AI assistant with encrypted memory, multi-provider LLM routing, and privacy-first design.

## Features

- **Encrypted Memory** - AES-256-GCM encryption for all stored data with PBKDF2 key derivation
- **Multi-Provider Routing** - Intelligent routing across Claude, OpenAI, Gemini, and Ollama
- **Vector Memory** - Long-term context using Qdrant with semantic search
- **Security-First** - Rate limiting, prompt injection detection, secrets management
- **Self-Hosted** - Run entirely on your own infrastructure with Ollama
- **Cost Tracking** - Monitor and budget API spending with alerts
- **User Profiles** - Learn preferences and adapt responses
- **Skills Framework** - Extensible task management, calendar, and more

## Quick Start

```bash
# Clone the repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai

# One-command deployment (interactive setup)
./start.sh  # or start.ps1 on Windows
```

See the [Installation Guide](INSTALLATION.md) for detailed platform-specific instructions.

## Documentation

### Getting Started

| Section | Description |
|---------|-------------|
| [Installation](INSTALLATION.md) | Platform-specific setup guide |
| [Configuration](CONFIGURATION.md) | All environment variables |
| [Hardware Recommendations](HARDWARE-RECOMMENDATIONS.md) | Optimize for your system |
| [Windows Deployment](WINDOWS_DEPLOYMENT.md) | Windows-specific guide |

### User Guides

| Section | Description |
|---------|-------------|
| [Commands](COMMANDS.md) | Discord slash commands reference |
| [FAQ](FAQ.md) | Frequently asked questions |
| [Troubleshooting](TROUBLESHOOTING.md) | Common issues and solutions |

### Advanced Features

| Section | Description |
|---------|-------------|
| [Features Overview](FEATURES.md) | Phase 5+ features guide |
| [Skills Framework](SKILLS.md) | Task management and extensibility |
| [Cost Tracking](COST_TRACKING.md) | Budget management and optimization |
| [Profile System](PROFILES.md) | User preference learning |

### Architecture & Security

| Section | Description |
|---------|-------------|
| [Architecture](ARCHITECTURE.md) | System design and components |
| [Docker Architecture](DOCKER_ARCHITECTURE.md) | Container setup and networking |
| [Security](SECURITY.md) | Security controls and best practices |

### Development

| Section | Description |
|---------|-------------|
| [Testing](TESTING.md) | Test suite and coverage |
| [Testing & Deployment](TESTING-DEPLOYMENT.md) | Comprehensive deployment validation |
| [CI/CD](CI_CD.md) | Continuous integration pipeline |
| [GitHub Secrets](GITHUB_SECRETS.md) | CI/CD secrets configuration |
| [Changelog](CHANGELOG.md) | Version history |
| [Contributing](CONTRIBUTING.md) | Contribution guidelines |
| [Development](DEVELOPMENT.md) | Developer documentation |

## Project Status

- Test Coverage: **78%** (885 unit + 14 integration + 4 E2E tests)
- Current Version: 3.0.0 (Fully Automated Docker Deployment)
- Features: Phase 5+ complete (encryption, cost tracking, profiles, skills)

## Links

- [GitHub Repository](https://github.com/jimtin/zetherion-ai)
- [Report Issues](https://github.com/jimtin/zetherion-ai/issues)
- [Wiki](https://github.com/jimtin/zetherion-ai/wiki)
- [Discussions](https://github.com/jimtin/zetherion-ai/discussions)
