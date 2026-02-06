#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Stopping Zetherion AI${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo ""

# Stop Ollama container
print_info "Stopping Ollama container..."
if docker ps --format '{{.Names}}' | grep -q "^zetherion_ai-ollama$"; then
    docker stop zetherion_ai-ollama
    print_success "Ollama container stopped"
else
    print_warning "Ollama container not running"
fi

# Stop Qdrant container
print_info "Stopping Qdrant container..."
if docker ps --format '{{.Names}}' | grep -q "^zetherion_ai-qdrant$"; then
    docker stop zetherion_ai-qdrant
    print_success "Qdrant container stopped"
else
    print_warning "Qdrant container not running"
fi

# Kill any running bot processes
print_info "Checking for running bot processes..."
if pgrep -f "python -m zetherion_ai" >/dev/null; then
    pkill -f "python -m zetherion_ai"
    print_success "Bot processes stopped"
else
    print_warning "No bot processes found"
fi

echo ""
print_success "Zetherion AI stopped successfully"
echo ""
