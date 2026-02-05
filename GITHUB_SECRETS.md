# GitHub Secrets Configuration

This document lists all GitHub secrets for SecureClaw's CI/CD pipeline.

> **⚠️ Important:** As of v1.1.0, **integration tests run locally only**. GitHub CI runs only unit tests, linting, type checking, security scans, and Docker builds. **No secrets are required** for CI/CD to pass.

## 📋 Quick Reference

| Secret Name | Required? | Purpose | Used In |
|-------------|-----------|---------|---------|
| `DISCORD_TOKEN` | ⚠️ Optional | Production bot token | Local integration tests only |
| `GEMINI_API_KEY` | ⚠️ Optional | Gemini API for routing & embeddings | Local integration tests only |
| `ANTHROPIC_API_KEY` | ⚠️ Optional | Claude API for complex tasks | Local integration tests only |
| `OPENAI_API_KEY` | ⚠️ Optional | OpenAI API for complex tasks | Local integration tests only |
| `TEST_DISCORD_BOT_TOKEN` | ⚠️ Optional | Test bot token for E2E tests | Local Discord E2E tests only |
| `TEST_DISCORD_CHANNEL_ID` | ⚠️ Optional | Test channel ID for E2E tests | Local Discord E2E tests only |

**All secrets are now optional** - they're only needed if you want to run integration tests locally.

---

## ✅ CI/CD Pipeline (No Secrets Required)

The GitHub Actions CI/CD pipeline runs:
- ✅ Linting & Formatting
- ✅ Type Checking
- ✅ Security Scanning
- ✅ Unit Tests (Python 3.12 & 3.13)
- ✅ Docker Build Test

**These require no secrets and will pass out of the box.**

---

## 🧪 Local Integration Testing (Optional)

### 1. DISCORD_TOKEN

**Purpose:** Your production Discord bot token for local integration testing.

**How to get it:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application
3. Go to "Bot" section
4. Click "Reset Token" or copy existing token
5. Copy the token immediately (it won't be shown again)

**Format:** Three dot-separated parts totaling 70+ characters

**Security Notes:**
- This token grants full access to your bot
- Never commit this to version control
- Regenerate if accidentally exposed

**Add to GitHub:**
```bash
Settings → Secrets and variables → Actions → New repository secret
Name: DISCORD_TOKEN
Value: <your-token-here>
```

---

### 2. GEMINI_API_KEY

**Purpose:** Google Gemini API key for routing, embeddings, and simple queries.

**How to get it:**
1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Click "Create API Key"
3. Select or create a Google Cloud project
4. Copy the API key

**Format:** `AIzaSy...` followed by 33 alphanumeric characters

**Free Tier:**
- 60 requests per minute
- 1,500 requests per day
- Sufficient for CI/CD testing

**Add to GitHub:**
```bash
Settings → Secrets and variables → Actions → New repository secret
Name: GEMINI_API_KEY
Value: <your-api-key-here>
```

---

## ⚠️ Optional Secrets

### 3. ANTHROPIC_API_KEY (Optional)

**Purpose:** Claude API for handling complex tasks and code generation.

**Required for:**
- Testing Claude-based response generation
- If not provided, tests will use Gemini for all queries

**How to get it:**
1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Navigate to "API Keys"
3. Click "Create Key"
4. Copy the API key

**Format:** `sk-ant-api03-` followed by 95-100 alphanumeric characters

**Pricing:**
- Pay-as-you-go
- Claude Sonnet 4.5: $3 per million input tokens, $15 per million output tokens
- CI usage: ~$0.10-0.50 per pipeline run

**Add to GitHub:**
```bash
Settings → Secrets and variables → Actions → New repository secret
Name: ANTHROPIC_API_KEY
Value: <your-api-key-here>
```

---

### 4. OPENAI_API_KEY (Optional)

**Purpose:** OpenAI API for alternative complex task handling.

**Required for:**
- Testing OpenAI-based response generation
- If not provided, tests will use Claude or Gemini

**How to get it:**
1. Go to [OpenAI Platform](https://platform.openai.com/api-keys)
2. Click "Create new secret key"
3. Name it (e.g., "SecureClaw CI")
4. Copy the API key immediately

**Format:** `sk-proj-` followed by ~48 alphanumeric characters

**Pricing:**
- Pay-as-you-go
- GPT-4o: $2.50 per million input tokens, $10 per million output tokens
- CI usage: ~$0.05-0.30 per pipeline run

**Add to GitHub:**
```bash
Settings → Secrets and variables → Actions → New repository secret
Name: OPENAI_API_KEY
Value: <your-api-key-here>
```

---

### 5. TEST_DISCORD_BOT_TOKEN (Optional)

**Purpose:** Separate Discord bot token for end-to-end testing with real Discord API.

**Required for:**
- Discord E2E tests (`test_discord_e2e.py`)
- Testing real bot responses, slash commands, and message handling
- If not provided, Discord E2E tests are skipped

**How to get it:**
1. Create a **separate** Discord application for testing
2. Go to [Discord Developer Portal](https://discord.com/developers/applications)
3. Click "New Application"
4. Name it "SecureClaw Test Bot"
5. Go to "Bot" section
6. Copy the bot token

**Important:**
- Use a DIFFERENT bot than your production bot
- Add this test bot to a dedicated test server
- Give it minimal permissions (Read Messages, Send Messages)

**Format:** Same as `DISCORD_TOKEN`

**Add to GitHub:**
```bash
Settings → Secrets and variables → Actions → New repository secret
Name: TEST_DISCORD_BOT_TOKEN
Value: <your-test-bot-token-here>
```

---

### 6. TEST_DISCORD_CHANNEL_ID (Optional)

**Purpose:** Discord channel ID where the test bot will send messages.

**Required for:**
- Discord E2E tests alongside `TEST_DISCORD_BOT_TOKEN`
- If not provided, Discord E2E tests are skipped

**How to get it:**
1. Enable Developer Mode in Discord:
   - User Settings → Advanced → Developer Mode (toggle ON)
2. Right-click the test channel
3. Click "Copy Channel ID"

**Format:** `1234567890123456789` (18-19 digits)

**Important:**
- Use a dedicated test channel
- The test bot must have access to this channel
- Messages will be posted during CI runs

**Add to GitHub:**
```bash
Settings → Secrets and variables → Actions → New repository secret
Name: TEST_DISCORD_CHANNEL_ID
Value: <your-channel-id-here>
```

---

## 🚀 Quick Setup Guide

### For GitHub CI/CD (No Setup Needed!)

**No secrets required!** The CI/CD pipeline runs unit tests, linting, type checking, security scans, and Docker builds - all without any secrets.

Just push your code and CI will pass automatically.

---

### For Local Integration Tests (Optional)

If you want to run integration tests locally, add these to your `.env` file:

```bash
# Required for local integration tests
DISCORD_TOKEN=<your-production-bot-token>
GEMINI_API_KEY=<your-gemini-api-key>

# Optional (improves test coverage)
ANTHROPIC_API_KEY=<your-claude-api-key>
OPENAI_API_KEY=<your-openai-api-key>
```

**Run with:** `./scripts/run-integration-tests.sh`

---

### For Local Discord E2E Tests (Optional)

To run Discord E2E tests locally with real Discord API:

```bash
# Required for Discord E2E tests
DISCORD_TOKEN=<your-production-bot-token>
GEMINI_API_KEY=<your-gemini-api-key>
TEST_DISCORD_BOT_TOKEN=<your-test-bot-token>
TEST_DISCORD_CHANNEL_ID=<your-test-channel-id>

# Optional but recommended
ANTHROPIC_API_KEY=<your-claude-api-key>
OPENAI_API_KEY=<your-openai-api-key>
```

**Run with:** `pytest tests/integration/test_discord_e2e.py -v -s -m discord_e2e`

---

## 📝 Adding Secrets to GitHub (Optional)

> **Note:** GitHub secrets are **optional** now since integration tests run locally only.

If you want to add secrets for future use:

### Via GitHub Web UI

1. Go to your repository on GitHub
2. Click **Settings** (top menu)
3. In the left sidebar, click **Secrets and variables** → **Actions**
4. Click **New repository secret**
5. Enter the **Name** (exactly as shown above)
6. Enter the **Value** (your API key/token)
7. Click **Add secret**

### Via GitHub CLI

```bash
# All secrets are optional for local testing
gh secret set DISCORD_TOKEN
gh secret set GEMINI_API_KEY
gh secret set ANTHROPIC_API_KEY
gh secret set OPENAI_API_KEY
gh secret set TEST_DISCORD_BOT_TOKEN
gh secret set TEST_DISCORD_CHANNEL_ID
```

You'll be prompted to paste the value for each secret.

---

## 🔍 Verifying CI/CD

### Check CI Status

Push a commit and check the GitHub Actions tab. You should see:

### Expected CI Output (No Secrets Needed)

All CI jobs should pass without any secrets:

```
✅ Linting & Formatting
✅ Type Checking
✅ Security Scanning
✅ Unit Tests (Python 3.12)
✅ Unit Tests (Python 3.13)
✅ Docker Build
✅ CI Summary

Note: Integration tests (E2E) run locally only.
```

---

## 🔐 Security Best Practices

### DO ✅

- Use separate test bot tokens from production
- Regenerate tokens if accidentally exposed
- Use API keys with minimal required permissions
- Monitor API usage dashboards for unexpected activity
- Set up billing alerts for paid APIs

### DON'T ❌

- Never commit secrets to version control
- Never share secrets in Discord, Slack, or email
- Never use production bot tokens in public CI
- Never skip secret rotation after team member departures
- Never set secrets as environment variables in CI config files

---

## 💰 Cost Estimates

### CI/CD Cost: $0/month

**GitHub CI/CD is completely free!** No API keys or secrets required.

### Local Integration Testing Cost

If you run integration tests locally:

| Service | Usage | Cost |
|---------|-------|------|
| Gemini API | 10-20 requests/test run | **Free** (within free tier) |
| Anthropic Claude | 5-10 requests/test run | ~$0.10-0.50/run |
| OpenAI GPT-4o | 5-10 requests/test run | ~$0.05-0.30/run |
| Discord API | Unlimited | **Free** |

**Typical cost:** $0-1/month for occasional local testing.

---

## 🆘 Troubleshooting

### CI is failing

**Cause:** Usually linting, type checking, or unit test failures.

**Solution:**
1. Run locally: `pytest tests/ -m "not integration"`
2. Check pre-commit hooks: `pre-commit run --all-files`
3. Review GitHub Actions logs for specific error

### Local integration tests failing with auth errors

**Cause:** `DISCORD_TOKEN` or `GEMINI_API_KEY` invalid or missing in `.env` file.

**Solution:**
1. Verify `.env` file exists and has required keys
2. Regenerate tokens if expired
3. Check for typos in environment variable names (case-sensitive!)

### API rate limit errors (local testing)

**Cause:** Too many local test runs hitting API limits.

**Solution:**
1. Wait for quotas to reset (Gemini: 60 seconds for per-minute limits)
2. Increase API quotas (Gemini: upgrade from free tier)
3. Run fewer tests: `pytest tests/integration/test_e2e.py::test_simple_question -v`

### "Secret not found" errors

**Cause:** Secret name mismatch or not set at repository level.

**Solution:**
- Secret names are **case-sensitive**
- Must be set at repository level (not environment)
- Use exact names from this guide

---

## 📚 Additional Resources

- [GitHub Encrypted Secrets Docs](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [Discord Developer Portal](https://discord.com/developers/docs)
- [Google AI Studio](https://makersuite.google.com/)
- [Anthropic Console](https://console.anthropic.com/)
- [OpenAI Platform](https://platform.openai.com/)

---

## 🔄 Last Updated

**Date:** 2026-02-06
**CI/CD Version:** v1.0.0
**SecureClaw Version:** Phases 1-4 complete

---

## Questions?

If you have issues with secrets configuration:
1. Check [Troubleshooting](#-troubleshooting) section above
2. Review [CI/CD Documentation](docs/CI_CD.md)
3. Open an issue on [GitHub](https://github.com/jimtin/sercureclaw/issues)
