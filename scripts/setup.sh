#!/bin/bash
# Initial setup script for a new machine
# Run this on MacBook Air after cloning the repo

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Zetherion AI Setup${NC}"
echo "================"

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker not found. Please install Docker Desktop first.${NC}"
    echo "https://www.docker.com/products/docker-desktop/"
    exit 1
fi

# Check for .env
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}No .env found. Creating from template...${NC}"
    cp .env.example .env
    echo ""
    echo "Please edit .env with your API keys:"
    echo "  - DISCORD_TOKEN"
    echo "  - GEMINI_API_KEY"
    echo "  - ANTHROPIC_API_KEY (optional)"
    echo ""
    echo "Then run this script again."
    exit 0
fi

# Check if .env has been configured
if grep -q "^DISCORD_TOKEN=$" .env; then
    echo -e "${YELLOW}Please configure .env with your API keys first.${NC}"
    exit 1
fi

# Build and start
echo -e "${YELLOW}Building and starting Zetherion AI...${NC}"
docker compose up -d --build

echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Commands:"
echo "  docker compose logs -f     # View logs"
echo "  docker compose restart     # Restart bot"
echo "  docker compose down        # Stop bot"
