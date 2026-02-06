#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Zetherion AI Status${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo ""

# Check Qdrant
print_info "Checking Qdrant..."
if docker ps --format '{{.Names}}' | grep -q "^secureclaw-qdrant$"; then
    if curl -s http://localhost:6333/healthz >/dev/null 2>&1; then
        print_success "Qdrant is running and healthy"
        # Get Qdrant info
        COLLECTIONS=$(curl -s http://localhost:6333/collections | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('result', {}).get('collections', [])))" 2>/dev/null || echo "0")
        echo "    Collections: $COLLECTIONS"
    else
        print_warning "Qdrant container is running but not responding"
    fi
elif docker ps -a --format '{{.Names}}' | grep -q "^secureclaw-qdrant$"; then
    print_warning "Qdrant container exists but is not running"
else
    print_error "Qdrant container not found"
fi

echo ""

# Check bot process
print_info "Checking bot process..."
if pgrep -f "python -m secureclaw" >/dev/null; then
    PID=$(pgrep -f "python -m secureclaw")
    print_success "Bot is running (PID: $PID)"

    # Check how long it's been running
    UPTIME=$(ps -p "$PID" -o etime= | xargs)
    echo "    Uptime: $UPTIME"
else
    print_error "Bot is not running"
fi

echo ""

# Check virtual environment
print_info "Checking virtual environment..."
if [ -d ".venv" ]; then
    print_success "Virtual environment exists"
else
    print_error "Virtual environment not found"
fi

echo ""

# Check .env file
print_info "Checking configuration..."
if [ -f ".env" ]; then
    print_success ".env file exists"

    # Source and check required vars (without printing them)
    source .env

    if [ -n "$DISCORD_TOKEN" ]; then
        print_success "Discord token configured"
    else
        print_error "Discord token missing"
    fi

    if [ -n "$GEMINI_API_KEY" ]; then
        print_success "Gemini API key configured"
    else
        print_error "Gemini API key missing"
    fi

    if [ -n "$ANTHROPIC_API_KEY" ]; then
        print_success "Anthropic API key configured (optional)"
    else
        print_warning "Anthropic API key not configured (optional)"
    fi

    if [ -n "$OPENAI_API_KEY" ]; then
        print_success "OpenAI API key configured (optional)"
    else
        print_warning "OpenAI API key not configured (optional)"
    fi
else
    print_error ".env file not found"
fi

echo ""

# Overall status
print_info "Overall Status:"
if docker ps --format '{{.Names}}' | grep -q "^secureclaw-qdrant$" && \
   pgrep -f "python -m secureclaw" >/dev/null; then
    print_success "Zetherion AI is fully operational"
else
    print_warning "Zetherion AI is not fully running"
    echo ""
    print_info "To start Zetherion AI, run: ./start.sh"
fi

echo ""
