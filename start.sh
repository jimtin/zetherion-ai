#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
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

print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo ""
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

print_header "SecureClaw Startup Script"

# 1. Check Python 3.12+
print_info "Checking Python version..."
if command_exists python3.12; then
    PYTHON_CMD="python3.12"
    print_success "Python 3.12 found"
elif command_exists python3.13; then
    PYTHON_CMD="python3.13"
    print_success "Python 3.13 found"
elif command_exists python3; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
    if [[ $(echo "$PYTHON_VERSION >= 3.12" | bc -l) -eq 1 ]]; then
        PYTHON_CMD="python3"
        print_success "Python $PYTHON_VERSION found"
    else
        print_error "Python 3.12+ required, found $PYTHON_VERSION"
        print_info "Install with: brew install python@3.12"
        exit 1
    fi
else
    print_error "Python 3.12+ not found"
    print_info "Install with: brew install python@3.12"
    exit 1
fi

# 2. Check Docker
print_info "Checking Docker..."
if ! command_exists docker; then
    print_error "Docker not found"
    print_info "Install Docker Desktop from: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# Check if Docker daemon is ready, launch if needed
if ! docker info >/dev/null 2>&1; then
    # Check if Docker Desktop process is running (might be starting up)
    if pgrep -x "Docker" > /dev/null; then
        print_warning "Docker Desktop is starting, waiting for daemon to be ready..."
    else
        # Docker not running at all, launch it
        print_info "Docker Desktop is not running, launching it..."

        # Verify Docker.app exists
        if [ ! -d "/Applications/Docker.app" ]; then
            print_error "Docker Desktop not found at /Applications/Docker.app"
            print_info "Please install Docker Desktop from: https://www.docker.com/products/docker-desktop"
            exit 1
        fi

        # Launch Docker Desktop
        if open -a Docker 2>/dev/null; then
            print_success "Docker Desktop launched"

            # Give Docker time to start launching
            echo "  Waiting for Docker process to initialize..."
            sleep 5

            # Verify process started
            if ! pgrep -x "Docker" > /dev/null; then
                print_error "Docker Desktop failed to start"
                print_info "Please start Docker Desktop manually and try again"
                exit 1
            fi

            # Quick check loop for fast machines (4 attempts with 5s waits)
            print_info "Docker is initializing, checking daemon..."
            for attempt in {1..4}; do
                if docker info >/dev/null 2>&1; then
                    print_success "Docker daemon is ready"
                    # Docker started quickly, we're done!
                    break 2  # Break out of both loops
                fi

                if [ $attempt -lt 4 ]; then
                    echo "  Attempt $attempt: Not ready yet, waiting 5 seconds..."
                    sleep 5
                else
                    echo "  Attempt $attempt: Not ready yet, continuing with extended wait..."
                    sleep 5
                fi
            done
        else
            print_error "Failed to launch Docker Desktop"
            print_info "Please start Docker Desktop manually and try again"
            exit 1
        fi

        print_info "Docker still initializing (this can take 30-60s on cold start)..."
    fi

    # Extended wait up to 90 seconds for Docker daemon (for slower machines or cold starts)
    for i in {1..90}; do
        if docker info >/dev/null 2>&1; then
            print_success "Docker daemon is ready"
            break
        fi

        # Show progress every 10 seconds
        if [ $((i % 10)) -eq 0 ]; then
            echo "  Still waiting... (${i}s)"
        fi

        sleep 1

        # If we've waited the full 90 seconds, give up
        if [ $i -eq 90 ]; then
            echo ""
            print_error "Docker daemon did not become ready after 90 seconds"
            print_info "Check Docker Desktop status in menu bar and try again"
            print_info "You may need to restart Docker Desktop manually"
            exit 1
        fi
    done
else
    print_success "Docker is running"
fi

# 3. Check .env file
print_info "Checking .env configuration..."
if [ ! -f .env ]; then
    print_error ".env file not found"
    print_info "Copy .env.example to .env and add your API keys"
    exit 1
fi

# Check required environment variables
source .env
MISSING_VARS=()

if [ -z "$DISCORD_TOKEN" ]; then
    MISSING_VARS+=("DISCORD_TOKEN")
fi
if [ -z "$GEMINI_API_KEY" ]; then
    MISSING_VARS+=("GEMINI_API_KEY")
fi

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    print_error "Missing required environment variables: ${MISSING_VARS[*]}"
    print_info "Please add them to your .env file"
    exit 1
fi
print_success ".env file configured"

# 3.5. Router Backend Selection
if [ -z "$ROUTER_BACKEND" ]; then
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Router Backend Selection${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo ""
    echo "SecureClaw can use two different backends for message routing:"
    echo ""
    echo "  1. ${GREEN}Gemini${NC} (Google) - Cloud-based, fast, minimal setup"
    echo "     • Uses your existing Gemini API key"
    echo "     • No additional downloads"
    echo "     • Recommended for cloud-based workflows"
    echo ""
    echo "  2. ${GREEN}Ollama${NC} (Local) - Privacy-focused, runs on your machine"
    echo "     • No data sent to external APIs for routing"
    echo "     • ~5GB model download (first time only)"
    echo "     • Recommended for privacy-conscious users"
    echo ""
    read -p "Which backend would you like to use? (1=Gemini, 2=Ollama) [1]: " -r
    echo ""

    case "$REPLY" in
        2)
            ROUTER_BACKEND="ollama"
            print_success "Selected: Ollama (local routing)"
            ;;
        1|"")
            ROUTER_BACKEND="gemini"
            print_success "Selected: Gemini (cloud routing)"
            ;;
        *)
            print_warning "Invalid selection, defaulting to Gemini"
            ROUTER_BACKEND="gemini"
            ;;
    esac

    # Save to .env
    echo "ROUTER_BACKEND=$ROUTER_BACKEND" >> .env
    print_info "Saved preference to .env"
    echo ""
fi

# 4. Set up virtual environment
print_info "Checking virtual environment..."
if [ ! -d ".venv" ]; then
    print_warning "Virtual environment not found, creating..."
    $PYTHON_CMD -m venv .venv
    print_success "Virtual environment created"
fi

# Activate virtual environment
source .venv/bin/activate
print_success "Virtual environment activated"

# 5. Check/install dependencies
print_info "Checking dependencies..."
if ! python -c "import discord" 2>/dev/null; then
    print_warning "Dependencies not installed, installing..."
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install -e .
    print_success "Dependencies installed"
else
    print_success "Dependencies already installed"
fi

# 6. Check/start Qdrant container
print_info "Checking Qdrant vector database..."
if docker ps -a --format '{{.Names}}' | grep -q "^secureclaw-qdrant$"; then
    if docker ps --format '{{.Names}}' | grep -q "^secureclaw-qdrant$"; then
        print_success "Qdrant container already running"
    else
        print_warning "Qdrant container exists but not running, starting..."
        docker start secureclaw-qdrant
        print_success "Qdrant container started"
    fi
else
    print_warning "Qdrant container not found, creating..."
    docker run -d \
        --name secureclaw-qdrant \
        -p 6333:6333 \
        -v "$(pwd)/qdrant_storage:/qdrant/storage" \
        qdrant/qdrant:latest
    print_success "Qdrant container created and started"
fi

# Wait for Qdrant to be ready
print_info "Waiting for Qdrant to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:6333/healthz >/dev/null 2>&1; then
        print_success "Qdrant is ready"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        print_error "Qdrant failed to start"
        exit 1
    fi
    sleep 1
done

# 7. Run system assessment for Ollama (if using Ollama backend)
if [ "$ROUTER_BACKEND" = "ollama" ]; then
    print_header "Ollama System Assessment"

    # Check if we should run assessment
    if [ ! -f ".ollama_assessed" ] || [ -z "$OLLAMA_ROUTER_MODEL" ]; then
        print_info "Running hardware assessment to recommend optimal model..."

        # Install psutil if needed (for better hardware detection)
        if ! $PYTHON_CMD -c "import psutil" 2>/dev/null; then
            print_info "Installing psutil for hardware detection..."
            pip install -q psutil
        fi

        # Run assessment
        $PYTHON_CMD scripts/assess-system.py

        echo ""
        read -p "Would you like to use the recommended model? (Y/n): " -r
        echo ""
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            print_info "Updating .env with recommended model..."
            $PYTHON_CMD scripts/assess-system.py --update-env

            # Reload .env to get updated model
            source .env

            print_success "Configuration updated!"
        else
            print_info "Using model from .env: ${OLLAMA_ROUTER_MODEL:-llama3.1:8b}"
        fi

        # Mark as assessed
        touch .ollama_assessed
    else
        print_info "Using configured model: ${OLLAMA_ROUTER_MODEL:-llama3.1:8b}"
        print_info "To reassess: rm .ollama_assessed && ./start.sh"
    fi
fi

# 8. Check/start Ollama container (if using Ollama backend)
if [ "$ROUTER_BACKEND" = "ollama" ]; then
    # Set OLLAMA_HOST to localhost for local development
    # (In Docker deployment, this would be "ollama" for the service name)
    if [ -z "$OLLAMA_HOST" ] || [ "$OLLAMA_HOST" = "ollama" ]; then
        export OLLAMA_HOST="localhost"
        # Update .env if it has the wrong value
        if grep -q "^OLLAMA_HOST=" .env 2>/dev/null; then
            sed -i '' 's/^OLLAMA_HOST=.*/OLLAMA_HOST=localhost/' .env
        else
            echo "OLLAMA_HOST=localhost" >> .env
        fi
    fi

    # Get required Docker memory from .env (set by assess-system.py)
    OLLAMA_DOCKER_MEMORY="${OLLAMA_DOCKER_MEMORY:-8}"  # Default to 8GB if not set

    # Check Docker memory allocation
    print_info "Checking Docker memory allocation..."
    DOCKER_TOTAL_MEMORY=$(docker info 2>/dev/null | grep "Total Memory" | awk '{print $3}')
    DOCKER_MEMORY_GB=$(echo "$DOCKER_TOTAL_MEMORY" | sed 's/GiB//')

    if [ ! -z "$DOCKER_MEMORY_GB" ]; then
        REQUIRED_MEMORY=$OLLAMA_DOCKER_MEMORY
        if (( $(echo "$DOCKER_MEMORY_GB < $REQUIRED_MEMORY" | bc -l) )); then
            echo ""
            print_warning "Docker has only ${DOCKER_MEMORY_GB}GB allocated"
            print_warning "Your selected model requires ${REQUIRED_MEMORY}GB"
            echo ""
            echo "What would you like to do?"
            echo "  1. Automatically increase Docker memory to ${REQUIRED_MEMORY}GB (recommended)"
            echo "  2. Choose a smaller model that fits current Docker memory"
            echo "  3. Continue anyway (may fail)"
            echo ""
            read -p "Enter choice (1/2/3) [1]: " -r
            echo ""

            case "${REPLY:-1}" in
                1)
                    print_info "Increasing Docker memory to ${REQUIRED_MEMORY}GB..."
                    echo ""
                    if ./scripts/increase-docker-memory.sh --yes; then
                        echo ""
                        print_success "Docker is ready with ${REQUIRED_MEMORY}GB memory"

                        # Quick sanity check that Docker is still responding
                        if ! docker info >/dev/null 2>&1; then
                            print_warning "Docker daemon not responding. Waiting a bit longer..."
                            sleep 10
                            if ! docker info >/dev/null 2>&1; then
                                print_error "Docker daemon still not ready. Please check Docker Desktop."
                                exit 1
                            fi
                        fi
                    else
                        echo ""
                        print_error "Failed to increase Docker memory"
                        echo "Please either:"
                        echo "  1. Manually increase Docker memory in Docker Desktop Settings"
                        echo "  2. Run ./start.sh again and choose a smaller model"
                        exit 1
                    fi
                    ;;
                2)
                    print_info "Removing assessment marker to choose a different model..."
                    rm -f .ollama_assessed
                    print_info "Please run ./start.sh again to choose a smaller model"
                    exit 0
                    ;;
                3)
                    print_warning "Continuing with insufficient memory. Model may crash."
                    ;;
                *)
                    print_error "Invalid choice"
                    exit 1
                    ;;
            esac
        else
            print_success "Docker memory: ${DOCKER_MEMORY_GB}GB (sufficient for ${REQUIRED_MEMORY}GB requirement)"
        fi
    fi

    print_info "Starting Ollama container..."

    if docker ps -a --format '{{.Names}}' | grep -q "^secureclaw-ollama$"; then
        if docker ps --format '{{.Names}}' | grep -q "^secureclaw-ollama$"; then
            print_success "Ollama container already running"
        else
            print_warning "Ollama container exists but not running, starting..."
            docker start secureclaw-ollama
            print_success "Ollama container started"
        fi
    else
        print_warning "Ollama container not found, creating..."
        docker run -d \
            --name secureclaw-ollama \
            --memory="${OLLAMA_DOCKER_MEMORY}g" \
            --memory-swap="${OLLAMA_DOCKER_MEMORY}g" \
            -p 11434:11434 \
            -v "$(pwd)/ollama_models:/root/.ollama" \
            ollama/ollama:latest
        print_success "Ollama container created and started (${OLLAMA_DOCKER_MEMORY}GB memory limit)"
    fi

    # Wait for Ollama to be ready
    print_info "Waiting for Ollama to be ready..."
    MAX_RETRIES=30
    RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            print_success "Ollama is ready"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
            print_error "Ollama failed to start"
            exit 1
        fi
        sleep 1
    done

    # Pull model if not already available
    OLLAMA_MODEL="${OLLAMA_ROUTER_MODEL:-llama3.1:8b}"
    print_info "Checking if model '$OLLAMA_MODEL' is available..."

    if docker exec secureclaw-ollama ollama list | grep -q "$OLLAMA_MODEL"; then
        print_success "Model '$OLLAMA_MODEL' already available"
    else
        print_warning "Model '$OLLAMA_MODEL' not found, downloading (this may take several minutes)..."
        print_info "Model size: ~4.7GB - please be patient..."

        if docker exec secureclaw-ollama ollama pull "$OLLAMA_MODEL"; then
            print_success "Model '$OLLAMA_MODEL' downloaded successfully"
        else
            print_error "Failed to download model '$OLLAMA_MODEL'"
            print_info "You can manually pull it later with: docker exec secureclaw-ollama ollama pull $OLLAMA_MODEL"
            print_warning "Continuing anyway - the bot will fall back to Gemini if the model isn't available"
        fi
    fi
else
    print_info "Using Gemini backend (ROUTER_BACKEND=${ROUTER_BACKEND:-gemini})"
fi

# 9. Final checks
print_header "Starting SecureClaw Bot"

print_info "Configuration Summary:"
echo "  • Python: $($PYTHON_CMD --version)"
echo "  • Discord Token: ${DISCORD_TOKEN:0:20}..."
echo "  • Gemini API: ${GEMINI_API_KEY:0:20}..."
echo "  • Anthropic API: ${ANTHROPIC_API_KEY:0:20}..."
echo "  • OpenAI API: ${OPENAI_API_KEY:0:20}..."
echo "  • Qdrant: http://localhost:6333"
echo "  • Router Backend: ${ROUTER_BACKEND:-gemini}"
if [ "$ROUTER_BACKEND" = "ollama" ]; then
    echo "  • Ollama: http://localhost:11434 (Model: ${OLLAMA_ROUTER_MODEL:-llama3.1:8b})"
fi
echo "  • File Logging: ${LOG_TO_FILE:-true} (Directory: ${LOG_DIRECTORY:-logs})"
echo "  • Allowed Users: ${ALLOWED_USER_IDS:-"All users (⚠ not recommended for production)"}"
echo ""

# 9. Start the bot
print_success "All checks passed! Starting bot..."
echo ""
echo -e "${GREEN}Press Ctrl+C to stop the bot${NC}"
echo ""

# Run the bot (set PYTHONPATH to include src directory)
PYTHONPATH="${PWD}/src:${PYTHONPATH}" python -m secureclaw
