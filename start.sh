#!/bin/bash

# Zetherion AI - Fully Automated Docker Deployment for Unix/Mac/Linux
# This script sets up and runs Zetherion AI entirely in Docker containers.
# It handles all prerequisites, configuration, and deployment automatically.

set -euo pipefail

# ============================================================
# PARSE ARGUMENTS
# ============================================================

SKIP_HARDWARE_ASSESSMENT=false
FORCE_REBUILD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-hardware-assessment)
            SKIP_HARDWARE_ASSESSMENT=true
            shift
            ;;
        --force-rebuild)
            FORCE_REBUILD=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Zetherion AI - Fully Automated Docker Deployment"
            echo ""
            echo "Options:"
            echo "  --skip-hardware-assessment    Skip hardware assessment and use default Ollama model"
            echo "  --force-rebuild               Force rebuild of Docker images even if they exist"
            echo "  -h, --help                    Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                            Standard deployment with hardware assessment"
            echo "  $0 --skip-hardware-assessment Deploy without hardware assessment"
            echo "  $0 --force-rebuild            Force rebuild all Docker images"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

# ============================================================
# HELPER FUNCTIONS
# ============================================================

print_header() {
    echo ""
    echo -e "\033[0;34m============================================================\033[0m"
    echo -e "\033[0;34m  $1\033[0m"
    echo -e "\033[0;34m============================================================\033[0m"
    echo ""
}

print_phase() {
    echo ""
    echo -e "\033[0;36m[PHASE] $1\033[0m"
    echo ""
}

print_success() {
    echo -e "\033[0;32m[OK] $1\033[0m"
}

print_failure() {
    echo -e "\033[0;31m[ERROR] $1\033[0m"
}

print_warning() {
    echo -e "\033[0;33m[WARNING] $1\033[0m"
}

print_info() {
    echo -e "\033[0;36m[INFO] $1\033[0m"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

get_disk_free_gb() {
    df -H . | tail -1 | awk '{print $4}' | sed 's/G//' | cut -d'.' -f1
}

# ============================================================
# PHASE 1: PREREQUISITES CHECK & AUTO-INSTALL
# ============================================================

print_header "Zetherion AI - Automated Docker Deployment"

print_phase "Phase 1/7: Checking Prerequisites"

# Detect OS
OS_TYPE="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS_TYPE="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS_TYPE="linux"
fi

print_info "Detected OS: $OS_TYPE"

# Check Docker Desktop
print_info "Checking Docker Desktop..."
if ! command_exists docker; then
    print_warning "Docker Desktop not found"

    read -p "Install Docker Desktop? (Y/n): " install
    if [[ "$install" =~ ^[Yy]?$ ]]; then
        print_info "Installing Docker Desktop..."

        if [[ "$OS_TYPE" == "macos" ]]; then
            if command_exists brew; then
                brew install --cask docker
                print_success "Docker Desktop installed"
            else
                print_failure "Homebrew not found. Please install Docker manually from: https://www.docker.com/products/docker-desktop"
                exit 1
            fi
        elif [[ "$OS_TYPE" == "linux" ]]; then
            print_info "Please install Docker from: https://docs.docker.com/engine/install/"
            exit 1
        fi

        print_warning "Please start Docker Desktop and run this script again"
        exit 0
    else
        print_failure "Docker Desktop is required"
        print_info "Install from: https://www.docker.com/products/docker-desktop"
        exit 1
    fi
fi

print_success "Docker Desktop is installed"

# Check if Docker daemon is running
print_info "Checking Docker daemon..."
if ! docker ps >/dev/null 2>&1; then
    print_warning "Docker daemon is not running"
    print_info "Starting Docker Desktop..."

    if [[ "$OS_TYPE" == "macos" ]]; then
        open -a Docker
    else
        print_info "Please start Docker manually"
    fi

    print_info "Waiting for Docker to start (max 60 seconds)..."
    max_wait=60
    waited=0
    while [ $waited -lt $max_wait ]; do
        sleep 2
        waited=$((waited + 2))
        if docker ps >/dev/null 2>&1; then
            print_success "Docker daemon is now running"
            break
        fi
        echo -n "."
    done

    if [ $waited -ge $max_wait ]; then
        echo ""
        print_failure "Docker failed to start within $max_wait seconds"
        print_info "Please start Docker Desktop manually and try again"
        exit 1
    fi
else
    print_success "Docker daemon is running"
fi

# Check Git
print_info "Checking Git..."
if ! command_exists git; then
    print_warning "Git not found"

    read -p "Install Git? (Y/n): " install
    if [[ "$install" =~ ^[Yy]?$ ]]; then
        print_info "Installing Git..."

        if [[ "$OS_TYPE" == "macos" ]]; then
            if command_exists brew; then
                brew install git
                print_success "Git installed"
            else
                print_info "Please install Git from: https://git-scm.com/download/mac"
                exit 1
            fi
        elif [[ "$OS_TYPE" == "linux" ]]; then
            if command_exists apt-get; then
                sudo apt-get update && sudo apt-get install -y git
                print_success "Git installed"
            elif command_exists yum; then
                sudo yum install -y git
                print_success "Git installed"
            else
                print_info "Please install Git from: https://git-scm.com/download/linux"
                exit 1
            fi
        fi
    else
        print_warning "Git not installed (optional for now)"
    fi
else
    print_success "Git is installed"
fi

# Check disk space
free_space=$(get_disk_free_gb)
print_info "Disk space: ${free_space}GB free"
if [ "$free_space" -lt 20 ]; then
    print_warning "Low disk space (less than 20GB free)"
    print_warning "Ollama models require 5-10GB of space"
else
    print_success "Sufficient disk space available"
fi

print_success "Prerequisites check complete"

# ============================================================
# PHASE 2: HARDWARE ASSESSMENT
# ============================================================

hardware_assessment=""

if [ "$SKIP_HARDWARE_ASSESSMENT" = false ]; then
    print_phase "Phase 2/7: Hardware Assessment"

    print_info "Building hardware assessment container..."
    if docker build -t zetherion-ai-assess:distroless -f Dockerfile.assess . >/dev/null 2>&1; then
        print_success "Assessment container built"

        print_info "Assessing system hardware..."
        assess_output=$(docker run --rm --entrypoint /usr/bin/python3.11 \
            zetherion-ai-assess:distroless /app/assess-system.py --json 2>&1 || true)

        if [ $? -eq 0 ] && echo "$assess_output" | python3 -m json.tool >/dev/null 2>&1; then
            hardware_assessment="$assess_output"

            # Display hardware info using python
            python3 <<EOF
import json
data = json.loads('''$hardware_assessment''')

hw = data.get('hardware', {})
rec = data.get('recommendation', {})

print("")
print("System Hardware:")
print(f"  CPU: {hw.get('cpu_model', 'Unknown')}")
if hw.get('cpu_count'):
    print(f"  Cores: {hw.get('cpu_count')} ({hw.get('cpu_threads')} threads)")
if hw.get('ram_gb'):
    print(f"  RAM: {hw.get('ram_gb')} GB total, {hw.get('available_ram_gb')} GB available")
print(f"  GPU: {hw.get('gpu', {}).get('name', 'None')}")

print("")
print("Recommended Ollama Model:")
print(f"  Model: {rec.get('model')}")
print(f"  Size: {rec.get('size_gb')} GB download")
print(f"  Quality: {rec.get('quality')}")
print(f"  Speed: {rec.get('inference_time')}")
print(f"  Reason: {rec.get('reason')}")

warnings = data.get('warnings', [])
if warnings:
    print("")
    print("Warnings:")
    for warning in warnings:
        print(f"  ⚠ {warning}")
EOF

            print_success "Hardware assessment complete"
        else
            print_warning "Hardware assessment failed, using defaults"
        fi
    else
        print_warning "Failed to build assessment container, skipping"
    fi
else
    print_info "Skipping hardware assessment (--skip-hardware-assessment)"
fi

# ============================================================
# PHASE 3: CONFIGURATION SETUP
# ============================================================

print_phase "Phase 3/7: Configuration Setup"

# Check if .env exists
if [ ! -f ".env" ]; then
    print_info ".env file not found"
    print_info "Starting interactive setup..."

    if ! python3 scripts/interactive-setup.py; then
        print_failure "Interactive setup failed"
        exit 1
    fi
    print_success "Configuration created"
else
    print_success ".env file exists"

    # Verify required keys
    if ! grep -q "^DISCORD_TOKEN=.\\+" .env; then
        print_failure "DISCORD_TOKEN not set in .env"
        print_info "Please configure .env or delete it to run setup again"
        exit 1
    fi

    if ! grep -q "^GEMINI_API_KEY=.\\+" .env; then
        print_failure "GEMINI_API_KEY not set in .env"
        print_info "Please configure .env or delete it to run setup again"
        exit 1
    fi

    print_success "Required configuration present"
fi

# Get router backend from .env
router_backend="gemini"
if grep -q "^ROUTER_BACKEND=" .env; then
    router_backend=$(grep "^ROUTER_BACKEND=" .env | cut -d'=' -f2 | tr -d ' ')
fi

print_info "Router backend: $router_backend"

# ============================================================
# PHASE 4: DOCKER BUILD & DEPLOY
# ============================================================

print_phase "Phase 4/7: Docker Build & Deploy"

# Build images
if [ "$FORCE_REBUILD" = true ]; then
    print_info "Force rebuild requested"
    docker-compose build --no-cache
else
    print_info "Building Docker images (if needed)..."
    docker-compose build
fi

if [ $? -ne 0 ]; then
    print_failure "Docker build failed"
    exit 1
fi

print_success "Docker images built"

# Start containers
print_info "Starting containers..."
docker-compose up -d

if [ $? -ne 0 ]; then
    print_failure "Failed to start containers"
    exit 1
fi

print_success "Containers started"

# Wait for health checks
print_info "Waiting for services to become healthy..."
max_wait=120
waited=0
services=("zetherion-ai-qdrant" "zetherion-ai-skills" "zetherion-ai-bot")

while [ $waited -lt $max_wait ]; do
    sleep 5
    waited=$((waited + 5))

    all_healthy=true
    for service in "${services[@]}"; do
        health=$(docker inspect --format='{{.State.Health.Status}}' "$service" 2>&1 || echo "unknown")
        if [ "$health" != "healthy" ]; then
            all_healthy=false
            echo -n "."
            break
        fi
    done

    if [ "$all_healthy" = true ]; then
        echo ""
        print_success "All services are healthy"
        break
    fi
done

if [ $waited -ge $max_wait ]; then
    echo ""
    print_warning "Services did not become healthy within $max_wait seconds"
    print_info "Check logs with: docker-compose logs"
fi

# ============================================================
# PHASE 5: MODEL DOWNLOAD (if Ollama)
# ============================================================

if [ "$router_backend" = "ollama" ]; then
    print_phase "Phase 5/7: Ollama Model Download"

    # Get model name from .env
    ollama_model="llama3.1:8b"
    if grep -q "^OLLAMA_ROUTER_MODEL=" .env; then
        ollama_model=$(grep "^OLLAMA_ROUTER_MODEL=" .env | cut -d'=' -f2 | tr -d ' ')
    fi

    print_info "Checking if model '$ollama_model' is already downloaded..."

    # Check if model exists
    if docker exec zetherion-ai-ollama ollama list 2>&1 | grep -q "$ollama_model"; then
        print_success "Model '$ollama_model' already downloaded"
    else
        print_info "Downloading model '$ollama_model'..."
        print_warning "This may take several minutes (5-10GB download)"

        # Pull model with progress
        docker exec zetherion-ai-ollama ollama pull "$ollama_model"

        if [ $? -eq 0 ]; then
            print_success "Model downloaded successfully"
        else
            print_failure "Model download failed"
            print_warning "You can download it later with: docker exec zetherion-ai-ollama ollama pull $ollama_model"
        fi
    fi
else
    print_info "Skipping model download (using Gemini for routing)"
fi

# ============================================================
# PHASE 6: VERIFICATION
# ============================================================

print_phase "Phase 6/7: Verification"

# Check all containers
print_info "Checking container status..."
containers=$(docker ps --format "table {{.Names}}\t{{.Status}}" | grep "zetherion" || true)

if [ -n "$containers" ]; then
    echo ""
    echo -e "\033[0;37mRunning Containers:\033[0m"
    echo "$containers" | while read -r line; do
        echo "  $line"
    done
    echo ""
fi

# Test Qdrant
print_info "Testing Qdrant connection..."
if curl -s http://localhost:6333/healthz >/dev/null 2>&1; then
    print_success "Qdrant is healthy"
else
    print_warning "Qdrant health check failed"
fi

# Test Ollama (if enabled)
if [ "$router_backend" = "ollama" ]; then
    print_info "Testing Ollama connection..."
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        print_success "Ollama is healthy"
    else
        print_warning "Ollama health check failed"
    fi
fi

# ============================================================
# PHASE 7: SUCCESS & NEXT STEPS
# ============================================================

print_phase "Phase 7/7: Deployment Complete"

echo ""
echo -e "\033[0;32m============================================================\033[0m"
echo -e "\033[0;32m  Zetherion AI is now running!\033[0m"
echo -e "\033[0;32m============================================================\033[0m"
echo ""

echo -e "\033[0;37mNext Steps:\033[0m"
echo -e "  \033[0;36m1. View logs:        docker-compose logs -f\033[0m"
echo -e "  \033[0;36m2. Check status:     ./status.sh\033[0m"
echo -e "  \033[0;36m3. Stop bot:         ./stop.sh\033[0m"
echo ""
echo -e "  \033[0;36m4. Invite bot to Discord:\033[0m"
echo -e "     \033[0;90mhttps://discord.com/developers/applications\033[0m"
echo ""

echo -e "\033[0;37mTroubleshooting:\033[0m"
echo -e "  \033[0;90m- Check container logs: docker-compose logs <service-name>\033[0m"
echo -e "  \033[0;90m- Restart services:     docker-compose restart\033[0m"
echo -e "  \033[0;90m- Full reset:           docker-compose down && ./start.sh\033[0m"
echo ""

print_success "Deployment successful!"
echo ""
