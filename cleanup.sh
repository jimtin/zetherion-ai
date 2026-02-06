#!/bin/bash

# Requires: bash 4.0+
# Description: Complete cleanup and reset of Zetherion AI

set -euo pipefail

# ============================================================
# HELPER FUNCTIONS
# ============================================================

print_success() { echo -e "\033[0;32m[OK] $1\033[0m"; }
print_failure() { echo -e "\033[0;31m[ERROR] $1\033[0m"; }
print_warning() { echo -e "\033[0;33m[WARNING] $1\033[0m"; }
print_info() { echo -e "\033[0;36m[INFO] $1\033[0m"; }

print_header() {
    echo ""
    echo -e "\033[0;31m============================================================\033[0m"
    echo -e "\033[0;31m  $1\033[0m"
    echo -e "\033[0;31m============================================================\033[0m"
    echo ""
}

# ============================================================
# PARSE ARGUMENTS
# ============================================================

KEEP_DATA=false
KEEP_CONFIG=false
REMOVE_OLD_VERSION=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --keep-config)
            KEEP_CONFIG=true
            shift
            ;;
        --remove-old-version)
            REMOVE_OLD_VERSION=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Complete cleanup and reset of Zetherion AI"
            echo ""
            echo "Options:"
            echo "  --keep-data              Keep Qdrant database and Ollama models (preserve data volumes)"
            echo "  --keep-config            Keep .env configuration file"
            echo "  --remove-old-version     Also remove old local Python installation artifacts (.venv, __pycache__, etc.)"
            echo "  -h, --help               Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                       Complete cleanup (will prompt for confirmation)"
            echo "  $0 --keep-data           Remove containers but keep data volumes"
            echo "  $0 --keep-config         Remove everything but keep .env file"
            echo "  $0 --remove-old-version  Also clean up old local Python artifacts"
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
# MAIN
# ============================================================

print_header "Zetherion AI - Complete Cleanup"

print_warning "This will remove all Docker containers, images, and optionally data!"
echo ""

# Summary of what will be removed
echo -e "\033[0;33mThe following will be removed:\033[0m"
echo -e "  \033[0;90m- All Zetherion AI Docker containers\033[0m"
echo -e "  \033[0;90m- All Zetherion AI Docker images\033[0m"

if [ "$KEEP_DATA" = false ]; then
    echo -e "  \033[0;90m- Qdrant database (all stored memories)\033[0m"
    echo -e "  \033[0;90m- Ollama models (will need to re-download)\033[0m"
else
    echo -e "  \033[0;32m- Data volumes will be KEPT\033[0m"
fi

if [ "$KEEP_CONFIG" = false ]; then
    echo -e "  \033[0;90m- .env configuration file\033[0m"
else
    echo -e "  \033[0;32m- .env file will be KEPT\033[0m"
fi

if [ "$REMOVE_OLD_VERSION" = true ]; then
    echo -e "  \033[0;90m- Old local Python artifacts (.venv, __pycache__)\033[0m"
fi

echo ""
read -p "Are you sure you want to continue? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    print_info "Cleanup cancelled"
    exit 0
fi

echo ""
print_info "Starting cleanup..."
echo ""

# ============================================================
# STEP 1: STOP AND REMOVE CONTAINERS
# ============================================================

print_info "Step 1: Stopping and removing containers..."

if docker ps -a --format "{{.Names}}" | grep -q "zetherion-ai"; then
    print_info "Stopping containers..."
    docker-compose down --timeout 30 >/dev/null 2>&1 || true
    print_success "Containers stopped and removed"
else
    print_info "No containers found"
fi

# ============================================================
# STEP 2: REMOVE VOLUMES
# ============================================================

if [ "$KEEP_DATA" = false ]; then
    print_info "Step 2: Removing data volumes..."

    print_warning "This will delete all stored data (memories, models)"
    read -p "Confirm data deletion? (yes/no): " confirm_data

    if [ "$confirm_data" = "yes" ]; then
        volumes=$(docker volume ls --format "{{.Name}}" | grep "zetherion" || true)

        if [ -n "$volumes" ]; then
            while IFS= read -r volume; do
                print_info "Removing volume: $volume"
                docker volume rm "$volume" >/dev/null 2>&1 || true
            done <<< "$volumes"
            print_success "Data volumes removed"
        else
            print_info "No volumes found"
        fi
    else
        print_info "Skipping data volume removal"
    fi
else
    print_info "Step 2: Keeping data volumes (--keep-data)"
fi

# ============================================================
# STEP 3: REMOVE DOCKER IMAGES
# ============================================================

print_info "Step 3: Removing Docker images..."

images=$(docker images --format "{{.Repository}}:{{.Tag}}" | grep "zetherion-ai" || true)

if [ -n "$images" ]; then
    while IFS= read -r image; do
        print_info "Removing image: $image"
        docker rmi "$image" -f >/dev/null 2>&1 || true
    done <<< "$images"
    print_success "Docker images removed"
else
    print_info "No images found"
fi

# ============================================================
# STEP 4: REMOVE CONFIGURATION
# ============================================================

if [ "$KEEP_CONFIG" = false ]; then
    print_info "Step 4: Removing configuration..."

    if [ -f ".env" ]; then
        print_warning "This will delete your .env configuration (API keys, etc.)"
        read -p "Confirm .env deletion? (yes/no): " confirm_config

        if [ "$confirm_config" = "yes" ]; then
            rm -f .env
            print_success ".env file removed"
        else
            print_info "Keeping .env file"
        fi
    else
        print_info "No .env file found"
    fi
else
    print_info "Step 4: Keeping .env file (--keep-config)"
fi

# ============================================================
# STEP 5: CLEAN UP LOCAL ARTIFACTS
# ============================================================

print_info "Step 5: Cleaning up local build artifacts..."

# Remove Python cache
if [ -d "__pycache__" ]; then
    rm -rf __pycache__
    print_success "Removed __pycache__"
fi

# Remove pytest cache
if [ -d ".pytest_cache" ]; then
    rm -rf .pytest_cache
    print_success "Removed .pytest_cache"
fi

# Remove local logs
if [ -d "logs" ] && [ "$(ls -A logs/*.log 2>/dev/null)" ]; then
    read -p "Remove local log files? (yes/no): " confirm_logs
    if [ "$confirm_logs" = "yes" ]; then
        rm -f logs/*.log
        print_success "Removed log files"
    fi
fi

# ============================================================
# STEP 6: REMOVE OLD LOCAL PYTHON VERSION (Optional)
# ============================================================

if [ "$REMOVE_OLD_VERSION" = true ]; then
    print_info "Step 6: Removing old local Python artifacts..."

    # Remove virtual environment
    if [ -d ".venv" ]; then
        print_info "Removing .venv directory..."
        rm -rf .venv
        print_success "Removed .venv"
    fi

    # Remove Python cache in src/
    if [ -d "src" ]; then
        find src -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find src -type f -name "*.pyc" -delete 2>/dev/null || true
        print_success "Removed Python cache directories and .pyc files"
    fi

    print_success "Old local Python artifacts removed"
else
    print_info "Step 6: Skipping old version cleanup (use --remove-old-version to enable)"
fi

# ============================================================
# SUMMARY
# ============================================================

echo ""
print_header "Cleanup Complete"

print_success "Zetherion AI has been cleaned up"
echo ""

echo -e "\033[0;37mNext steps:\033[0m"
echo -e "  \033[0;36m1. To reinstall: ./start.sh\033[0m"
if [ "$KEEP_CONFIG" = false ]; then
    echo -e "  \033[0;36m2. You'll need to reconfigure API keys\033[0m"
fi
if [ "$KEEP_DATA" = false ]; then
    echo -e "  \033[0;36m3. Ollama models will need to be re-downloaded\033[0m"
fi

echo ""

# Show what remains
print_info "Remaining Docker resources:"
remaining_containers=$(docker ps -a --format "{{.Names}}" | grep "zetherion" | wc -l | tr -d ' ')
remaining_volumes=$(docker volume ls --format "{{.Name}}" | grep "zetherion" | wc -l | tr -d ' ')
remaining_images=$(docker images --format "{{.Repository}}" | grep "zetherion-ai" | wc -l | tr -d ' ')

echo "  Containers: $remaining_containers"
echo "  Volumes: $remaining_volumes"
echo "  Images: $remaining_images"

if [ "$KEEP_CONFIG" = true ] && [ -f ".env" ]; then
    echo -e "  \033[0;32mConfig: .env file preserved\033[0m"
fi

echo ""
