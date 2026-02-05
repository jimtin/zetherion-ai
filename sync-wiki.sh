#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Syncing Documentation to GitHub Wiki${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo ""

# Get repository name from git remote
REPO_URL=$(git config --get remote.origin.url)
if [[ -z "$REPO_URL" ]]; then
    echo -e "${YELLOW}Warning: No git remote found${NC}"
    read -p "Enter your GitHub repo (e.g., username/secureclaw): " REPO_NAME
else
    # Extract username/repo from URL
    if [[ $REPO_URL == *"github.com"* ]]; then
        REPO_NAME=$(echo "$REPO_URL" | sed -E 's#.*github\.com[:/](.+)\.git#\1#')
    else
        read -p "Enter your GitHub repo (e.g., username/secureclaw): " REPO_NAME
    fi
fi

WIKI_URL="https://github.com/${REPO_NAME}.wiki.git"

echo -e "${BLUE}Repository:${NC} $REPO_NAME"
echo -e "${BLUE}Wiki URL:${NC} $WIKI_URL"
echo ""

# Check if wiki directory exists
if [ -d "wiki" ]; then
    echo -e "${GREEN}✓${NC} Wiki directory found, pulling latest changes..."
    cd wiki
    git pull
    cd ..
else
    echo -e "${BLUE}ℹ${NC} Cloning wiki repository..."
    git clone "$WIKI_URL" wiki
fi

# Copy documentation files
echo -e "${BLUE}ℹ${NC} Syncing documentation files..."

# Map docs files to wiki pages (remove .md, add to root)
cp docs/TROUBLESHOOTING.md wiki/Troubleshooting.md
cp docs/FAQ.md wiki/FAQ.md
cp docs/COMMANDS.md wiki/Commands.md

# Create Home page
cat > wiki/Home.md << EOF
# SecureClaw Wiki

Welcome to the SecureClaw documentation!

## Getting Started
- [Setup Guide](https://github.com/${REPO_NAME}#setup-guide)
- [Quick Start](https://github.com/${REPO_NAME}#quick-start)

## Documentation
- [Command Reference](Commands) - All Discord commands for testing
- [Troubleshooting Guide](Troubleshooting) - Common issues and solutions
- [FAQ](FAQ) - Frequently asked questions

## Quick Links
- [Test Commands](Commands#testing-checklist)
- [Discord Setup](Troubleshooting#discord-errors)
- [Configuration Issues](Troubleshooting#configuration-issues)

## Support
- [Report Issues](https://github.com/${REPO_NAME}/issues)
- [Ask Questions](https://github.com/${REPO_NAME}/discussions)

---
*Last synced: $(date)*
EOF

# Commit and push changes
cd wiki
echo -e "${BLUE}ℹ${NC} Committing changes..."

git add .
if git diff --staged --quiet; then
    echo -e "${YELLOW}⚠${NC} No changes to sync"
else
    git commit -m "Sync documentation from main repo [$(date +%Y-%m-%d)]"
    echo -e "${BLUE}ℹ${NC} Pushing to GitHub wiki..."
    git push
    echo ""
    echo -e "${GREEN}✓${NC} Wiki synced successfully!"
    echo -e "${GREEN}✓${NC} View at: https://github.com/${REPO_NAME}/wiki"
fi

cd ..
echo ""
