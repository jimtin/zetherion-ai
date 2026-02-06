#!/bin/bash

# Description: Stop Zetherion AI Docker containers

set -euo pipefail

# ============================================================
# HELPER FUNCTIONS
# ============================================================

print_success() { echo -e "\033[0;32m[OK] $1\033[0m"; }
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

print_header "Stopping Zetherion AI"

print_info "Stopping Docker containers..."
docker-compose down --timeout 30

if [ $? -eq 0 ]; then
    print_success "All containers stopped"
    echo ""
    print_info "To start again:  ./start.sh"
    print_info "To view status:  ./status.sh"
    echo ""
else
    print_warning "Failed to stop some containers"
    print_info "Check status with: docker-compose ps"
fi
