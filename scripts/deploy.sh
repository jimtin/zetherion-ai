#!/bin/bash
# Zetherion AI Deployment Script
# Deploys to MacBook Air from MacBook Pro

set -e

# Configuration - UPDATE THESE
REMOTE_HOST="macbook-air.local"  # or IP address
REMOTE_USER="jameshinton"        # your username on MacBook Air
REMOTE_PATH="~/Documents/Developer/PersonalBot"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Zetherion AI Deployment${NC}"
echo "====================="

# Check if .env exists locally
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found locally${NC}"
    echo "Create one with: cp .env.example .env"
    exit 1
fi

# Menu
echo ""
echo "What would you like to do?"
echo "1) Sync secrets only (.env)"
echo "2) Full deploy (git pull + .env + restart)"
echo "3) Just restart remote bot"
echo "4) Check remote status"
echo ""
read -p "Choice [1-4]: " choice

case $choice in
    1)
        echo -e "${YELLOW}Syncing .env to $REMOTE_HOST...${NC}"
        scp .env "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH/.env"
        echo -e "${GREEN}Done! .env synced.${NC}"
        ;;
    2)
        echo -e "${YELLOW}Full deploy to $REMOTE_HOST...${NC}"

        # Sync .env
        echo "  → Syncing .env..."
        scp .env "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH/.env"

        # Git pull and restart
        echo "  → Pulling latest code and restarting..."
        ssh "$REMOTE_USER@$REMOTE_HOST" "cd $REMOTE_PATH && git pull && docker compose down && docker compose up -d --build"

        echo -e "${GREEN}Done! Bot deployed and running.${NC}"
        ;;
    3)
        echo -e "${YELLOW}Restarting bot on $REMOTE_HOST...${NC}"
        ssh "$REMOTE_USER@$REMOTE_HOST" "cd $REMOTE_PATH && docker compose restart"
        echo -e "${GREEN}Done! Bot restarted.${NC}"
        ;;
    4)
        echo -e "${YELLOW}Checking status on $REMOTE_HOST...${NC}"
        ssh "$REMOTE_USER@$REMOTE_HOST" "cd $REMOTE_PATH && docker compose ps"
        ;;
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac
