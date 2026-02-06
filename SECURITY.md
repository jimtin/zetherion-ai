# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, use [GitHub Security Advisories](https://github.com/jimtin/zetherion-ai/security/advisories/new) to report vulnerabilities privately.

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

| Severity | Acknowledgement | Fix Timeline |
|----------|-----------------|--------------|
| Critical | Within 24 hours | 72 hours     |
| High     | Within 48 hours | 14 days      |
| Medium   | Within 7 days   | 30 days      |
| Low      | Within 14 days  | 90 days      |

## Security Documentation

For a comprehensive overview of Zetherion AI's security controls, testing strategy, and CI/CD pipeline, see [docs/SECURITY.md](docs/SECURITY.md).

## Security Features

Zetherion AI implements multiple layers of security:

- **Input validation**: Prompt injection detection with 17+ regex patterns
- **Access control**: User allowlist + per-user rate limiting
- **Secret management**: Pydantic SecretStr, never logged or serialised
- **Static analysis**: Bandit, CodeQL, Semgrep in CI
- **Container scanning**: Trivy for CVE detection
- **Dependency auditing**: pip-audit + Dependabot
- **Secret scanning**: Gitleaks pre-commit hooks
