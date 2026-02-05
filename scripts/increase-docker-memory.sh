#!/bin/bash
# Script to increase Docker Desktop memory allocation
# macOS only

set -e

# Parse arguments
AUTO_YES=false
if [[ "$1" == "--yes" ]] || [[ "$1" == "-y" ]]; then
    AUTO_YES=true
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

if [[ "$AUTO_YES" == false ]]; then
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Docker Memory Configuration${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo ""
fi

# Check if running on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "${RED}✗${NC} This script is for macOS only"
    exit 1
fi

# Docker Desktop settings file location
SETTINGS_FILE="$HOME/Library/Group Containers/group.com.docker/settings.json"

if [ ! -f "$SETTINGS_FILE" ]; then
    echo -e "${RED}✗${NC} Docker Desktop settings file not found"
    echo "Expected location: $SETTINGS_FILE"
    echo ""
    echo "Please configure Docker Desktop manually:"
    echo "  1. Open Docker Desktop"
    echo "  2. Go to Settings → Resources → Advanced"
    echo "  3. Set Memory to at least 10GB"
    echo "  4. Click 'Apply & Restart'"
    exit 1
fi

# Backup settings
BACKUP_FILE="${SETTINGS_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
cp "$SETTINGS_FILE" "$BACKUP_FILE"
echo -e "${GREEN}✓${NC} Backed up settings to: $BACKUP_FILE"

# Get current memory setting (in bytes)
CURRENT_MEMORY=$(cat "$SETTINGS_FILE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('memoryMiB', 2048))")
CURRENT_GB=$(echo "scale=1; $CURRENT_MEMORY / 1024" | bc)

echo ""
echo "Current Docker memory: ${CURRENT_GB}GB"
echo ""

# Get required memory from .env (set by assess-system.py)
if [ -f "../.env" ]; then
    source "../.env"
    REQUIRED_GB="${OLLAMA_DOCKER_MEMORY:-8}"
else
    REQUIRED_GB=8
fi

RECOMMENDED_MIB=$((REQUIRED_GB * 1024))

if [[ "$AUTO_YES" == false ]]; then
    echo "Required for your selected model: ${REQUIRED_GB}GB"
    echo ""
    read -p "Set Docker memory to ${REQUIRED_GB}GB? (Y/n): " -n 1 -r
    echo ""
fi

if [[ "$AUTO_YES" == true ]] || [[ ! $REPLY =~ ^[Nn]$ ]]; then
    # Update settings using Python
    echo -e "${GREEN}✓${NC} Setting Docker memory to ${REQUIRED_GB}GB..."
    python3 << EOF
import json

with open('$SETTINGS_FILE', 'r') as f:
    settings = json.load(f)

settings['memoryMiB'] = $RECOMMENDED_MIB

with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2)

print("✓ Updated Docker settings")
EOF

    if [[ "$AUTO_YES" == false ]]; then
        echo ""
        echo -e "${YELLOW}⚠${NC}  Docker Desktop needs to restart for changes to take effect"
        echo ""
        read -p "Restart Docker Desktop now? (Y/n): " -n 1 -r
        echo ""
    fi

    if [[ "$AUTO_YES" == true ]] || [[ ! $REPLY =~ ^[Nn]$ ]]; then
        # Check if Docker Desktop is running
        DOCKER_WAS_RUNNING=false
        if pgrep -x "Docker" > /dev/null; then
            DOCKER_WAS_RUNNING=true
            echo "Stopping Docker Desktop to apply memory changes..."
            echo "  Current status: Running"

            # Try osascript first
            if osascript -e 'quit app "Docker"' 2>/dev/null; then
                echo "  Sent quit signal via osascript"
            else
                echo -e "${YELLOW}⚠${NC}  Could not quit Docker via osascript, trying killall..."
                if killall Docker 2>/dev/null; then
                    echo "  Sent quit signal via killall"
                fi
            fi

            # Wait for Docker to fully stop
            echo "  Waiting for Docker to stop..."
            for i in {1..20}; do
                if ! pgrep -x "Docker" > /dev/null; then
                    echo -e "${GREEN}✓${NC} Docker stopped successfully"
                    break
                fi
                if [ $i -eq 20 ]; then
                    echo -e "${RED}✗${NC} Docker did not stop after 20 seconds"
                    echo "Please quit Docker Desktop manually and run this script again"
                    exit 1
                fi
                sleep 1
            done
        else
            echo "Docker Desktop needs to be started to apply memory changes..."
            echo "  Current status: Not running"
        fi

        # Verify Docker.app exists
        if [ ! -d "/Applications/Docker.app" ]; then
            echo -e "${RED}✗${NC} Docker Desktop not found at /Applications/Docker.app"
            echo "Please install Docker Desktop from https://www.docker.com/products/docker-desktop"
            exit 1
        fi

        # Start Docker Desktop
        echo "  Starting Docker Desktop..."

        # Try to launch Docker
        if open -a Docker 2>&1; then
            echo -e "${GREEN}✓${NC} Docker launch command succeeded"

            # Give Docker a moment to start launching
            echo "  Waiting for Docker process to start..."
            sleep 3

            # Verify Docker process started
            if ! pgrep -x "Docker" > /dev/null; then
                echo -e "${YELLOW}⚠${NC}  Docker process not detected yet, waiting longer..."
                sleep 3
            fi
        else
            echo -e "${RED}✗${NC} Failed to launch Docker with 'open -a Docker'"
            echo "  Attempting to open Docker.app directly..."
            if ! open -a /Applications/Docker.app 2>&1; then
                echo -e "${RED}✗${NC} Failed to launch Docker Desktop"
                echo ""
                echo "Please try starting Docker Desktop manually:"
                echo "  1. Open Finder"
                echo "  2. Go to Applications"
                echo "  3. Double-click Docker.app"
                echo ""
                echo "If Docker Desktop won't start, try:"
                echo "  - Check System Preferences → Security & Privacy"
                echo "  - Reinstall Docker Desktop from https://www.docker.com/products/docker-desktop"
                exit 1
            fi
            sleep 3
        fi

        # Final check that Docker process is running
        if pgrep -x "Docker" > /dev/null; then
            echo -e "${GREEN}✓${NC} Docker Desktop process is running"
        else
            echo -e "${RED}✗${NC} Docker Desktop process did not start"
            echo "Check Activity Monitor to see if Docker is running"
            exit 1
        fi

        # Wait for Docker daemon to be ready
        echo "  Waiting for Docker daemon to be ready..."
        echo "  (This can take 30-60 seconds on first start)"
        for i in {1..60}; do
            if docker info >/dev/null 2>&1; then
                echo -e "${GREEN}✓${NC} Docker daemon is ready"
                echo ""
                echo -e "${GREEN}✓${NC} Docker Desktop restarted successfully with ${REQUIRED_GB}GB memory"
                if [[ "$AUTO_YES" == false ]]; then
                    echo "You can now run ./start.sh to start SecureClaw"
                fi
                exit 0
            fi

            # Show progress every 5 seconds
            if [ $((i % 5)) -eq 0 ]; then
                echo "  Still waiting for daemon... (${i}s elapsed)"
            fi

            sleep 1
        done

        # Timeout waiting for Docker to be ready
        echo ""
        echo -e "${YELLOW}⚠${NC}  Docker Desktop started but daemon not ready after 60 seconds"
        echo ""
        echo "What to do next:"
        echo "  1. Check the Docker icon in your menu bar - it may still be starting"
        echo "  2. Wait until you see 'Docker Desktop is running' in the menu"
        echo "  3. Then run ./start.sh to continue"
        echo ""
        echo "If Docker never finishes starting:"
        echo "  - Check Activity Monitor for Docker processes"
        echo "  - Try quitting and restarting Docker manually"
        echo "  - Check Console.app for Docker error logs"
        exit 1
    else
        echo ""
        echo "Please restart Docker Desktop manually:"
        echo "  Docker menu → Quit Docker Desktop"
        echo "  Then reopen Docker Desktop"
    fi
else
    echo "Skipping memory update"
    echo ""
    echo "To manually update:"
    echo "  1. Open Docker Desktop"
    echo "  2. Go to Settings → Resources → Advanced"
    echo "  3. Set Memory to at least 10GB"
    echo "  4. Click 'Apply & Restart'"
fi

echo ""
