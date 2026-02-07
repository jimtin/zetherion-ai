#!/bin/bash

# Description: Check Zetherion AI container status

set +e  # Don't exit on errors for status checks

# ============================================================
# HELPER FUNCTIONS
# ============================================================

print_success() { echo -e "\033[0;32m[OK] $1\033[0m"; }
print_failure() { echo -e "\033[0;31m[ERROR] $1\033[0m"; }
print_warning() { echo -e "\033[0;33m[WARNING] $1\033[0m"; }
print_info() { echo -e "\033[0;36m[INFO] $1\033[0m"; }

print_header() {
    echo ""
    echo -e "\033[0;34m============================================================\033[0m"
    echo -e "\033[0;34m  $1\033[0m"
    echo -e "\033[0;34m============================================================\033[0m"
    echo ""
}

# ============================================================
# MAIN
# ============================================================

print_header "Zetherion AI Status"

# Check Qdrant
print_info "Checking Qdrant..."
qdrant_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-qdrant$" || true)

if [ -n "$qdrant_running" ]; then
    if curl -s http://localhost:6333/healthz >/dev/null 2>&1; then
        print_success "Qdrant is running and healthy"

        # Get collection count
        collections=$(curl -s http://localhost:6333/collections 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('result', {}).get('collections', [])))" 2>/dev/null || echo "Unable to retrieve")
        echo "    Collections: $collections"
    else
        print_warning "Qdrant container is running but not responding"
    fi
else
    qdrant_exists=$(docker ps -a --format "{{.Names}}" | grep -E "^zetherion-ai-qdrant$" || true)

    if [ -n "$qdrant_exists" ]; then
        print_warning "Qdrant container exists but is not running"
    else
        print_failure "Qdrant container not found"
    fi
fi

echo ""

# Check Ollama Router Container (for fast routing)
print_info "Checking Ollama Router Container..."
ollama_router_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-ollama-router$" || true)

if [ -n "$ollama_router_running" ]; then
    # Router container is internal only (no port exposed to host), check via docker exec
    router_health=$(docker exec zetherion-ai-ollama-router curl -s http://localhost:11434/api/tags 2>&1 || echo '{"error": "failed"}')
    if echo "$router_health" | grep -q "models"; then
        print_success "Ollama Router is running and healthy"

        # Get model list
        model_count=$(echo "$router_health" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('models', [])))" 2>/dev/null || echo "0")
        echo "    Models: $model_count (router models)"

        if [ "$model_count" != "0" ] && [ "$model_count" != "Unable to retrieve" ]; then
            models=$(echo "$router_health" | python3 -c "import sys, json; data=json.load(sys.stdin); [print(f'      - {m[\"name\"]}') for m in data.get('models', [])]" 2>/dev/null || true)
            if [ -n "$models" ]; then
                echo "$models"
            fi
        fi
    else
        print_warning "Ollama Router container is running but not responding"
    fi
else
    ollama_router_exists=$(docker ps -a --format "{{.Names}}" | grep -E "^zetherion-ai-ollama-router$" || true)

    if [ -n "$ollama_router_exists" ]; then
        print_warning "Ollama Router container exists but is not running"
    else
        print_info "Ollama Router container not found (optional, used with ROUTER_BACKEND=ollama)"
    fi
fi

echo ""

# Check Ollama Generation Container (for complex queries + embeddings)
print_info "Checking Ollama Generation Container..."
ollama_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-ollama$" || true)

if [ -n "$ollama_running" ]; then
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        print_success "Ollama Generation is running and healthy"

        # Get model list
        model_count=$(curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('models', [])))" 2>/dev/null || echo "Unable to retrieve")
        echo "    Models: $model_count (generation + embedding models)"

        if [ "$model_count" != "0" ] && [ "$model_count" != "Unable to retrieve" ]; then
            models=$(curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); [print(f'      - {m[\"name\"]}') for m in data.get('models', [])]" 2>/dev/null || true)
            if [ -n "$models" ]; then
                echo "$models"
            fi
        fi
    else
        print_warning "Ollama Generation container is running but not responding"
    fi
else
    ollama_exists=$(docker ps -a --format "{{.Names}}" | grep -E "^zetherion-ai-ollama$" || true)

    if [ -n "$ollama_exists" ]; then
        print_warning "Ollama Generation container exists but is not running"
    else
        print_info "Ollama Generation container not found (optional, used with ROUTER_BACKEND=ollama)"
    fi
fi

echo ""

# Check Skills Service
print_info "Checking Skills service..."
skills_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-skills$" || true)

if [ -n "$skills_running" ]; then
    skills_health=$(docker inspect --format='{{.State.Health.Status}}' zetherion-ai-skills 2>&1 || echo "unknown")

    if [ "$skills_health" = "healthy" ]; then
        print_success "Skills service is running and healthy"
    elif [ "$skills_health" = "starting" ]; then
        print_info "Skills service is starting..."
    else
        print_warning "Skills service is running but unhealthy"
    fi
else
    skills_exists=$(docker ps -a --format "{{.Names}}" | grep -E "^zetherion-ai-skills$" || true)

    if [ -n "$skills_exists" ]; then
        print_warning "Skills container exists but is not running"
    else
        print_failure "Skills container not found"
    fi
fi

echo ""

# Check Bot
print_info "Checking bot..."
bot_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-bot$" || true)

if [ -n "$bot_running" ]; then
    bot_health=$(docker inspect --format='{{.State.Health.Status}}' zetherion-ai-bot 2>&1 || echo "unknown")

    if [ "$bot_health" = "healthy" ]; then
        print_success "Bot is running and healthy"

        # Get uptime
        start_time=$(docker inspect --format='{{.State.StartedAt}}' zetherion-ai-bot 2>&1 || true)
        if [ -n "$start_time" ]; then
            # Calculate uptime (simplified)
            uptime_seconds=$(python3 -c "from datetime import datetime; start=datetime.fromisoformat('$start_time'.replace('Z', '+00:00')); now=datetime.now(start.tzinfo); diff=(now-start).total_seconds(); d=int(diff//86400); h=int((diff%86400)//3600); m=int((diff%3600)//60); s=int(diff%60); print(f'{d}d {h}h {m}m {s}s')" 2>/dev/null || echo "")
            if [ -n "$uptime_seconds" ]; then
                echo "    Uptime: $uptime_seconds"
            fi
        fi
    elif [ "$bot_health" = "starting" ]; then
        print_info "Bot is starting..."
    else
        print_warning "Bot is running but unhealthy"
    fi
else
    bot_exists=$(docker ps -a --format "{{.Names}}" | grep -E "^zetherion-ai-bot$" || true)

    if [ -n "$bot_exists" ]; then
        print_warning "Bot container exists but is not running"
    else
        print_failure "Bot container not found"
    fi
fi

echo ""

# Overall status
print_info "Overall Status:"
qdrant_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-qdrant$" || true)
bot_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-bot$" || true)
skills_running=$(docker ps --format "{{.Names}}" | grep -E "^zetherion-ai-skills$" || true)

if [ -n "$qdrant_running" ] && [ -n "$bot_running" ] && [ -n "$skills_running" ]; then
    print_success "Zetherion AI is fully operational"
else
    print_warning "Zetherion AI is not fully running"
    echo ""
    print_info "To start Zetherion AI, run: ./start.sh"
fi

echo ""

# Show container list
print_info "Container Summary:"
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "zetherion|NAMES" || echo "No Zetherion AI containers found"

echo ""
