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
cp docs/ARCHITECTURE.md wiki/Architecture.md
cp docs/SECURITY.md wiki/Security.md
cp docs/TESTING.md wiki/Testing.md
cp docs/CI_CD.md wiki/CI-CD.md
cp docs/DOCKER_ARCHITECTURE.md wiki/Docker-Architecture.md
cp docs/STARTUP_WALKTHROUGH.md wiki/Startup-Walkthrough.md
cp docs/TROUBLESHOOTING.md wiki/Troubleshooting.md
cp docs/FAQ.md wiki/FAQ.md
cp docs/COMMANDS.md wiki/Commands.md

# Copy root-level guides
cp CONTRIBUTING.md wiki/Contributing.md
cp DEVELOPMENT.md wiki/Development.md
cp CHANGELOG.md wiki/Changelog.md

# Create Home page
cat > wiki/Home.md << EOF
# SecureClaw Wiki

Welcome to the SecureClaw documentation! This wiki provides comprehensive guides for users, contributors, and developers.

## 🚀 Getting Started
- [Setup Guide](https://github.com/${REPO_NAME}#setup-guide) - Initial setup and configuration
- [Quick Start](https://github.com/${REPO_NAME}#quick-start) - Get running in minutes
- [Startup Walkthrough](Startup-Walkthrough) - Detailed startup script explanation

## 📖 User Documentation
- [Command Reference](Commands) - All Discord slash commands
- [FAQ](FAQ) - Frequently asked questions
- [Troubleshooting Guide](Troubleshooting) - Common issues and solutions

## 🏗️ Architecture & Design
- [Architecture Overview](Architecture) - System architecture and design patterns
- [Docker Architecture](Docker-Architecture) - Container setup and networking
- [Security](Security) - Security controls and testing

## 🧪 Development & Testing
- [Contributing Guide](Contributing) - How to contribute to SecureClaw
- [Development Guide](Development) - Advanced developer documentation
- [Testing Guide](Testing) - Testing patterns and coverage
- [CI/CD Pipeline](CI-CD) - Continuous integration and deployment

## 📝 Project Information
- [Changelog](Changelog) - Version history and recent changes
- Test Coverage: **87.58%** (255 unit + 14 integration + 4 E2E tests)
- Latest Release: v1.0.0 (Phases 1-4 complete)

## 🔗 Quick Links
- [Test Commands Checklist](Commands#testing-checklist)
- [Discord Bot Setup](Troubleshooting#discord-errors)
- [Configuration Issues](Troubleshooting#configuration-issues)
- [Docker Troubleshooting](Troubleshooting#docker-issues)
- [Pre-commit Hooks](Contributing#pre-commit-workflow)

## 💬 Support & Community
- [Report Issues](https://github.com/${REPO_NAME}/issues)
- [Ask Questions](https://github.com/${REPO_NAME}/discussions)
- [View Source Code](https://github.com/${REPO_NAME})

---
*Documentation last synced: $(date)*
*SecureClaw v1.0.0 | Test Coverage: 87.58%*
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
