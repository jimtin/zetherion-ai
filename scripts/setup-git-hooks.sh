#!/bin/bash
# Setup script for Git hooks and pre-commit framework
# Run this after cloning the repository

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Create log directory and timestamped log file
LOG_DIR="git-hook-logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup-$(date +%Y%m%d-%H%M%S).log"

# Function to log messages (both to console and file)
log() {
    echo -e "$@" | tee -a "$LOG_FILE"
}

# Function to log commands (capture output and errors)
log_command() {
    local cmd="$@"
    echo "$ $cmd" >> "$LOG_FILE"
    if $cmd >> "$LOG_FILE" 2>&1; then
        return 0
    else
        return 1
    fi
}

log ""
log "${BLUE}═══════════════════════════════════════════════${NC}"
log "${BLUE}  Git Hooks Setup${NC}"
log "${BLUE}═══════════════════════════════════════════════${NC}"
log ""
log "${BLUE}Logging to:${NC} $LOG_FILE"
log ""

# Check if we're in the project root
if [ ! -f "pyproject.toml" ]; then
    log "${RED}✗${NC} Error: Must run from project root"
    exit 1
fi

# Check if virtual environment is active
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -d ".venv" ]; then
        log "${YELLOW}⚠${NC} Activating virtual environment..."
        source .venv/bin/activate
        echo "Virtual environment activated" >> "$LOG_FILE"
    else
        log "${RED}✗${NC} Virtual environment not found"
        log "  Run: python3.12 -m venv .venv && source .venv/bin/activate"
        exit 1
    fi
fi

# Step 1: Install pre-commit framework
log "${BLUE}1/3${NC} Installing pre-commit framework..."
if log_command pip install pre-commit; then
    log "${GREEN}✓${NC} pre-commit installed"
else
    log "${RED}✗${NC} Failed to install pre-commit"
    log "See log file for details: $LOG_FILE"
    exit 1
fi

# Step 2: Install pre-commit hooks
log ""
log "${BLUE}2/3${NC} Installing pre-commit hooks..."
if log_command pre-commit install --hook-type pre-commit --hook-type pre-push; then
    log "${GREEN}✓${NC} pre-commit hooks installed"
else
    log "${RED}✗${NC} Failed to install pre-commit hooks"
    log "See log file for details: $LOG_FILE"
    exit 1
fi

# Step 3: Install custom pre-push hook
log ""
log "${BLUE}3/3${NC} Installing custom pre-push hook..."

# Make the custom hook executable
echo "$ chmod +x .git-hooks/pre-push" >> "$LOG_FILE"
chmod +x .git-hooks/pre-push 2>> "$LOG_FILE"

# Create symlink in .git/hooks/
if [ -L .git/hooks/pre-push ]; then
    echo "Removing existing symlink" >> "$LOG_FILE"
    rm .git/hooks/pre-push
fi

echo "$ ln -sf ../../.git-hooks/pre-push .git/hooks/pre-push" >> "$LOG_FILE"
ln -sf ../../.git-hooks/pre-push .git/hooks/pre-push 2>> "$LOG_FILE"

if [ -L .git/hooks/pre-push ]; then
    log "${GREEN}✓${NC} Custom pre-push hook installed"
else
    log "${RED}✗${NC} Failed to create symlink for pre-push hook"
    log "See log file for details: $LOG_FILE"
    exit 1
fi

# Optional: Run pre-commit on all files to verify setup
log ""
log "${YELLOW}⚠${NC} Would you like to run pre-commit checks on all files now?"
log "  This will ensure everything is properly configured."
read -p "Run checks? (y/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    log ""
    log "${BLUE}Running pre-commit on all files...${NC}"
    echo "" >> "$LOG_FILE"
    echo "===== PRE-COMMIT RUN OUTPUT =====" >> "$LOG_FILE"
    if pre-commit run --all-files 2>&1 | tee -a "$LOG_FILE"; then
        log ""
        log "${GREEN}✓${NC} All checks passed!"
    else
        log ""
        log "${YELLOW}⚠${NC} Some files needed formatting or had issues"
        log "  Changes may have been auto-fixed. Review and commit them."
        log "  ${BLUE}Check log file for full details: $LOG_FILE${NC}"
    fi
    echo "===== END PRE-COMMIT OUTPUT =====" >> "$LOG_FILE"
fi

log ""
log "${GREEN}═══════════════════════════════════════════════${NC}"
log "${GREEN}  Git Hooks Setup Complete! ✓${NC}"
log "${GREEN}═══════════════════════════════════════════════${NC}"
log ""
log "Git hooks are now active:"
log "  • ${GREEN}Pre-commit${NC}: Runs linting & formatting before each commit"
log "  • ${GREEN}Pre-push${NC}: Runs full test suite before each push"
log ""
log "To manually run pre-commit checks:"
log "  ${BLUE}pre-commit run --all-files${NC}"
log ""
log "To bypass hooks (not recommended):"
log "  ${YELLOW}git commit --no-verify${NC}"
log "  ${YELLOW}git push --no-verify${NC}"
log ""
log "${BLUE}Full log saved to:${NC} $LOG_FILE"
log ""
