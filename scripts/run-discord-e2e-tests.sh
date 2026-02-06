#!/bin/bash
# Discord E2E test runner for Zetherion AI
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Zetherion AI Discord E2E Tests${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}✗ .env file not found${NC}"
    echo "Please create a .env file with required configuration"
    exit 1
fi

# Load environment variables
set -a
source .env
set +a

# Check Discord E2E test requirements
if [ -z "$TEST_DISCORD_BOT_TOKEN" ] || [ -z "$TEST_DISCORD_CHANNEL_ID" ]; then
    echo -e "${YELLOW}⚠️  Discord E2E tests require TEST_DISCORD_BOT_TOKEN and TEST_DISCORD_CHANNEL_ID${NC}"
    echo ""
    echo "To run Discord E2E tests, you need:"
    echo "  1. Create a separate test bot in Discord Developer Portal"
    echo "  2. Add TEST_DISCORD_BOT_TOKEN to your .env file"
    echo "  3. Create a test Discord server/channel"
    echo "  4. Add TEST_DISCORD_CHANNEL_ID to your .env file"
    echo ""
    echo "See docs/TESTING.md for detailed setup instructions"
    exit 1
fi

echo -e "${GREEN}✓ Discord E2E test configuration found${NC}"
echo ""

# Check if Discord bot is running
echo -e "${BLUE}Checking if Zetherion AI bot is running...${NC}"
if ! docker ps | grep -q "secureclaw-bot"; then
    echo -e "${YELLOW}⚠️  Zetherion AI bot not running${NC}"
    echo "Starting bot with ./start.sh..."
    ./start.sh &
    STARTED_BOT=1

    # Wait for bot to be ready
    echo "Waiting for bot to connect to Discord..."
    sleep 15
fi

echo -e "${GREEN}✓ Bot is running${NC}"
echo ""

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo -e "${BLUE}Activating virtual environment...${NC}"
    source .venv/bin/activate
    echo -e "${GREEN}✓ Virtual environment activated${NC}"
    echo ""
elif [ -d "venv" ]; then
    echo -e "${BLUE}Activating virtual environment...${NC}"
    source venv/bin/activate
    echo -e "${GREEN}✓ Virtual environment activated${NC}"
    echo ""
fi

# Install test dependencies if needed
if ! python -c "import pytest" 2>/dev/null || ! python -c "import discord" 2>/dev/null; then
    echo -e "${YELLOW}Installing test dependencies...${NC}"
    pip install -q pytest pytest-asyncio discord.py
    echo -e "${GREEN}✓ Test dependencies installed${NC}"
    echo ""
fi

# Run Discord E2E tests
echo -e "${BLUE}Running Discord E2E tests...${NC}"
echo -e "${YELLOW}Note: These tests send real messages through Discord${NC}"
echo ""

# Set Python path
export PYTHONPATH="${PWD}/src:${PYTHONPATH}"

# Run pytest with discord_e2e marker
if pytest tests/integration/test_discord_e2e.py -v -s -m discord_e2e --tb=short; then
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  All Discord E2E Tests Passed! ✓${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    EXIT_CODE=0
else
    echo ""
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    echo -e "${RED}  Discord E2E Tests Failed ✗${NC}"
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}Tip: Check bot logs for more details:${NC}"
    echo "  docker logs secureclaw-bot"
    EXIT_CODE=1
fi

# Cleanup if we started the bot
if [ ! -z "$STARTED_BOT" ]; then
    echo ""
    echo -e "${BLUE}Stopping bot (was started by test script)...${NC}"
    ./stop.sh
fi

exit $EXIT_CODE
