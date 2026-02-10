# GitHub Secrets Configuration

This document lists all GitHub secrets for Zetherion AI's CI/CD pipeline and local development.

> **Important:** All secrets are optional for CI. The GitHub Actions pipeline runs unit tests, linting, type checking, security scans, and Docker builds without any configured secrets.

---

## Quick Reference

| Secret Name | Required? | Purpose | Used In |
|-------------|-----------|---------|---------|
| `DISCORD_TOKEN` | Optional | Production bot token | Local integration tests only |
| `GEMINI_API_KEY` | Optional | Gemini API for routing and embeddings | Local integration tests only |
| `ANTHROPIC_API_KEY` | Optional | Claude API for complex tasks | Local integration tests only |
| `OPENAI_API_KEY` | Optional | OpenAI API for complex tasks | Local integration tests only |
| `TEST_DISCORD_BOT_TOKEN` | Optional | Test bot token for E2E tests | Local Discord E2E tests only |
| `TEST_DISCORD_CHANNEL_ID` | Optional | Test channel ID for E2E tests | Local Discord E2E tests only |

All 6 secrets are optional. They are only needed if you want to run integration or E2E tests locally.

---

## CI/CD Pipeline (No Secrets Required)

The GitHub Actions CI/CD pipeline runs the following jobs without any secrets:

- Linting and formatting (Ruff)
- Type checking (Mypy strict mode)
- Security scanning (Bandit)
- Unit tests (Python 3.12 and 3.13) -- 3,000+ tests, 93%+ coverage
- Docker build verification (all 6 services)

These will pass out of the box on any fork or clone.

See [`../technical/architecture.md`](../technical/architecture.md) for how these jobs fit into the overall system.

---

## Local Integration Testing (Optional)

### DISCORD_TOKEN

**Purpose:** Your production Discord bot token for local integration testing.

**How to get it:**

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application
3. Go to "Bot" section
4. Click "Reset Token" or copy existing token
5. Copy the token immediately (it will not be shown again)

**Format:** Three dot-separated parts totaling 70+ characters.

**Security notes:**

- This token grants full access to your bot
- Never commit this to version control
- Regenerate immediately if accidentally exposed

---

### GEMINI_API_KEY

**Purpose:** Google Gemini API key for routing, embeddings, and simple queries. Zetherion AI uses gemini-2.5-flash as one of its default cloud models.

**How to get it:**

1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Click "Create API Key"
3. Select or create a Google Cloud project
4. Copy the API key

**Format:** `AIzaSy...` followed by 33 alphanumeric characters.

**Free tier:**

- 60 requests per minute
- 1,500 requests per day
- Sufficient for local testing

---

## Optional Secrets

### ANTHROPIC_API_KEY

**Purpose:** Claude API for handling complex tasks, code generation, and high-quality reasoning. Zetherion AI uses claude-sonnet-4-5-20250929 as its primary cloud model.

**Required for:**

- Testing Claude-based response generation
- If not provided, tests will fall back to Gemini or OpenAI

**How to get it:**

1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Navigate to "API Keys"
3. Click "Create Key"
4. Copy the API key

**Format:** `sk-ant-api03-` followed by 95-100 alphanumeric characters.

**Pricing:**

- Pay-as-you-go
- Claude Sonnet 4.5: $3 per million input tokens, $15 per million output tokens
- Local test usage: approximately $0.10-0.50 per test run

---

### OPENAI_API_KEY

**Purpose:** OpenAI API for alternative complex task handling. Zetherion AI uses gpt-5.2 as one of its default cloud models.

**Required for:**

- Testing OpenAI-based response generation
- If not provided, tests will fall back to Claude or Gemini

**How to get it:**

1. Go to [OpenAI Platform](https://platform.openai.com/api-keys)
2. Click "Create new secret key"
3. Name it (e.g., "Zetherion AI CI")
4. Copy the API key immediately

**Format:** `sk-proj-` followed by approximately 48 alphanumeric characters.

**Pricing:**

- Pay-as-you-go
- gpt-5.2: check [OpenAI pricing page](https://openai.com/pricing) for current rates
- Local test usage: approximately $0.05-0.30 per test run

---

### TEST_DISCORD_BOT_TOKEN

**Purpose:** Separate Discord bot token for end-to-end testing with the real Discord API.

**Required for:**

- Discord E2E tests (`test_discord_e2e.py`)
- Testing real bot responses, slash commands, and message handling
- If not provided, Discord E2E tests are skipped automatically

**How to get it:**

1. Create a **separate** Discord application for testing
2. Go to [Discord Developer Portal](https://discord.com/developers/applications)
3. Click "New Application"
4. Name it "Zetherion AI Test Bot"
5. Go to "Bot" section
6. Copy the bot token

**Important:**

- Use a DIFFERENT bot than your production bot
- Add this test bot to a dedicated test server
- Give it minimal permissions (Read Messages, Send Messages)

**Format:** Same as `DISCORD_TOKEN`.

---

### TEST_DISCORD_CHANNEL_ID

**Purpose:** Discord channel ID where the test bot will send messages during E2E testing.

**Required for:**

- Discord E2E tests alongside `TEST_DISCORD_BOT_TOKEN`
- If not provided, Discord E2E tests are skipped automatically

**How to get it:**

1. Enable Developer Mode in Discord: User Settings -> Advanced -> Developer Mode (toggle ON)
2. Right-click the test channel
3. Click "Copy Channel ID"

**Format:** `1234567890123456789` (18-19 digit numeric string).

**Important:**

- Use a dedicated test channel
- The test bot must have access to this channel
- Messages will be posted during test runs

---

## Quick Setup Guide

### For CI/CD (No Setup Needed)

No secrets required. Push your code and the CI pipeline will pass automatically. All 3,000+ unit tests run without any API keys.

### For Local Integration Tests

Add these to your `.env` file in the project root:

```bash
# Required for local integration tests
DISCORD_TOKEN=<your-production-bot-token>
GEMINI_API_KEY=<your-gemini-api-key>

# Optional (improves test coverage of multi-provider routing)
ANTHROPIC_API_KEY=<your-claude-api-key>
OPENAI_API_KEY=<your-openai-api-key>
```

Run with:

```bash
./scripts/run-integration-tests.sh
```

### For Discord E2E Tests

Add these additional variables to your `.env` file:

```bash
# Required for Discord E2E tests
TEST_DISCORD_BOT_TOKEN=<your-test-bot-token>
TEST_DISCORD_CHANNEL_ID=<your-test-channel-id>
```

Run with:

```bash
pytest tests/integration/test_discord_e2e.py -v -s -m discord_e2e
```

See [`../technical/configuration.md`](../technical/configuration.md) for the full list of environment variables.

---

## Adding Secrets to GitHub

> **Note:** GitHub secrets are optional since integration tests run locally only. These steps are provided for future use or if your workflow changes.

### Via GitHub Web UI

1. Go to your repository on GitHub
2. Click **Settings** (top menu)
3. In the left sidebar, click **Secrets and variables** -> **Actions**
4. Click **New repository secret**
5. Enter the **Name** (exactly as shown in the Quick Reference table)
6. Enter the **Value** (your API key or token)
7. Click **Add secret**

### Via GitHub CLI

```bash
gh secret set DISCORD_TOKEN
gh secret set GEMINI_API_KEY
gh secret set ANTHROPIC_API_KEY
gh secret set OPENAI_API_KEY
gh secret set TEST_DISCORD_BOT_TOKEN
gh secret set TEST_DISCORD_CHANNEL_ID
```

You will be prompted to paste the value for each secret.

---

## Verifying CI/CD

Push a commit and check the GitHub Actions tab. All CI jobs should pass without any secrets configured:

```
Linting and Formatting        PASS
Type Checking                 PASS
Security Scanning             PASS
Unit Tests (Python 3.12)      PASS
Unit Tests (Python 3.13)      PASS
Docker Build                  PASS
CI Summary                    PASS

Note: Integration tests (E2E) run locally only.
```

---

## Security Best Practices

**DO:**

- Use separate test bot tokens from production
- Regenerate tokens immediately if accidentally exposed
- Use API keys with minimal required permissions
- Monitor API usage dashboards for unexpected activity
- Set up billing alerts for paid APIs (Anthropic, OpenAI)
- Store secrets in `.env` (which is in `.gitignore`)

**DO NOT:**

- Commit secrets to version control
- Share secrets in Discord, Slack, or email
- Use production bot tokens in public CI
- Skip secret rotation after team member departures
- Set secrets as plaintext environment variables in CI config files
- Use the same API key for production and testing

---

## Cost Estimates

### CI/CD Cost: $0/month

GitHub CI/CD requires no API keys. All 3,000+ unit tests, linting, type checking, security scans, and Docker builds run for free.

### Local Integration Testing Cost

If you run integration tests locally:

| Service | Model | Usage | Cost |
|---------|-------|-------|------|
| Gemini API | gemini-2.5-flash | 10-20 requests/test run | **Free** (within free tier) |
| Anthropic Claude | claude-sonnet-4-5-20250929 | 5-10 requests/test run | ~$0.10-0.50/run |
| OpenAI | gpt-5.2 | 5-10 requests/test run | ~$0.05-0.30/run |
| Discord API | -- | Unlimited | **Free** |
| Ollama (local) | llama3.2:1b, llama3.1:8b | Unlimited | **Free** (runs locally) |

**Typical cost:** $0-1/month for occasional local testing.

See [`../technical/cost-tracking.md`](../technical/cost-tracking.md) for the built-in cost tracking and budget management system.

---

## Troubleshooting

### CI is failing

**Cause:** Usually linting, type checking, or unit test failures -- not missing secrets.

**Solution:**

1. Run locally: `pytest tests/ -m "not integration"`
2. Check pre-commit hooks: `pre-commit run --all-files`
3. Review GitHub Actions logs for the specific error message

### Local integration tests failing with auth errors

**Cause:** `DISCORD_TOKEN` or `GEMINI_API_KEY` invalid or missing in `.env` file.

**Solution:**

1. Verify `.env` file exists in the project root and has the required keys
2. Regenerate tokens if expired
3. Check for typos in environment variable names (they are case-sensitive)

### API rate limit errors (local testing)

**Cause:** Too many local test runs hitting API limits.

**Solution:**

1. Wait for quotas to reset (Gemini: 60 seconds for per-minute limits)
2. Increase API quotas (Gemini: upgrade from free tier)
3. Run fewer tests: `pytest tests/integration/test_e2e.py::test_simple_question -v`

### "Secret not found" errors

**Cause:** Secret name mismatch or not set at repository level.

**Solution:**

- Secret names are case-sensitive
- Must be set at repository level (not environment level)
- Use exact names from the Quick Reference table above

---

## Additional Resources

- [GitHub Encrypted Secrets Documentation](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [Discord Developer Portal](https://discord.com/developers/docs)
- [Google AI Studio](https://makersuite.google.com/)
- [Anthropic Console](https://console.anthropic.com/)
- [OpenAI Platform](https://platform.openai.com/)

---

## Last Updated

**Date:** 2026-02-08
**Version:** 3.0.0
**Zetherion AI:** Phases 1-9 complete

---

## Questions?

If you have issues with secrets configuration:

1. Check the [Troubleshooting](#troubleshooting) section above
2. Review the [CI/CD documentation](ci-cd.md)
3. Open an issue on [GitHub](https://github.com/jimtin/zetherion-ai/issues)
