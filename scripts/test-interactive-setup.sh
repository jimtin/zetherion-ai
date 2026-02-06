#!/bin/bash
# Test script for interactive-setup.py
# Simulates user input to test the setup flow

set -e

echo "Testing Interactive Setup Script"
echo "================================"
echo ""

# Backup existing .env if it exists
if [ -f .env ]; then
    echo "Backing up existing .env to .env.backup"
    cp .env .env.backup
fi

# Test 1: Minimal setup (Gemini router)
echo "Test 1: Minimal setup with Gemini router"
echo "========================================="
echo ""

cat << 'EOF' | python3 scripts/interactive-setup.py
y
YOUR_DISCORD_TOKEN_HERE
YOUR_GEMINI_API_KEY_HERE
n
n
1
EOF

if [ -f .env ]; then
    echo "✓ .env file created"

    # Verify required fields
    if grep -q "DISCORD_TOKEN=YOUR_DISCORD" .env; then
        echo "✓ Discord token set"
    else
        echo "✗ Discord token missing"
        exit 1
    fi

    if grep -q "GEMINI_API_KEY=YOUR_GEMINI" .env; then
        echo "✓ Gemini API key set"
    else
        echo "✗ Gemini API key missing"
        exit 1
    fi

    if grep -q "ROUTER_BACKEND=gemini" .env; then
        echo "✓ Router backend set to gemini"
    else
        echo "✗ Router backend not set correctly"
        exit 1
    fi

    echo ""
    echo "Test 1: PASSED"
else
    echo "✗ .env file not created"
    exit 1
fi

# Restore backup if it existed
if [ -f .env.backup ]; then
    echo ""
    echo "Restoring original .env from backup"
    mv .env.backup .env
else
    echo ""
    echo "Cleaning up test .env"
    rm .env
fi

echo ""
echo "All tests passed!"
