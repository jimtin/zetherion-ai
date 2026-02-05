#!/bin/bash
# Integration test runner for SecureClaw
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SecureClaw Integration Tests${NC}"
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

# Check required environment variables
REQUIRED_VARS=("GEMINI_API_KEY" "DISCORD_TOKEN")
MISSING_VARS=()

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    echo -e "${RED}✗ Missing required environment variables:${NC}"
    for var in "${MISSING_VARS[@]}"; do
        echo "  - $var"
    done
    echo ""
    echo "Please add these to your .env file"
    exit 1
fi

echo -e "${GREEN}✓ Environment variables validated${NC}"
echo ""

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}✗ Docker is not running${NC}"
    echo "Please start Docker Desktop and try again"
    exit 1
fi

echo -e "${GREEN}✓ Docker is running${NC}"
echo ""

# Clean up any existing test containers
echo -e "${BLUE}Cleaning up any existing test containers...${NC}"
docker compose -p secureclaw-test down -v >/dev/null 2>&1 || true
echo -e "${GREEN}✓ Cleanup complete${NC}"
echo ""

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo -e "${BLUE}Activating virtual environment...${NC}"
    source venv/bin/activate
    echo -e "${GREEN}✓ Virtual environment activated${NC}"
    echo ""
fi

# Install test dependencies if needed
if ! python -c "import pytest" 2>/dev/null; then
    echo -e "${YELLOW}Installing test dependencies...${NC}"
    pip install -q pytest pytest-asyncio
    echo -e "${GREEN}✓ Test dependencies installed${NC}"
    echo ""
fi

# Run integration tests
echo -e "${BLUE}Running integration tests...${NC}"
echo -e "${YELLOW}Note: This will start Docker containers and may take 2-3 minutes${NC}"
echo ""

# Set Python path
export PYTHONPATH="${PWD}/src:${PYTHONPATH}"

# Run pytest with integration marker
if pytest tests/integration/test_e2e.py -v -s -m integration --tb=short; then
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  All Integration Tests Passed! ✓${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    echo -e "${RED}  Integration Tests Failed ✗${NC}"
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}Tip: Check Docker logs for more details:${NC}"
    echo "  docker compose -p secureclaw-test logs secureclaw"
    echo "  docker compose -p secureclaw-test logs qdrant"
    exit 1
fi
