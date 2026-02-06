#!/bin/bash
# Generate self-signed TLS certificates for Qdrant
# These are for internal Docker network communication only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CERTS_DIR="$PROJECT_ROOT/data/certs/qdrant"

# Certificate validity (days)
VALIDITY_DAYS=3650  # 10 years for internal certs

echo "Generating TLS certificates for Qdrant..."
echo "Output directory: $CERTS_DIR"

# Create directory structure
mkdir -p "$CERTS_DIR"

# Check if certificates already exist
if [[ -f "$CERTS_DIR/server.crt" && -f "$CERTS_DIR/server.key" ]]; then
    echo "Certificates already exist. To regenerate, delete:"
    echo "  $CERTS_DIR/server.crt"
    echo "  $CERTS_DIR/server.key"
    exit 0
fi

# Generate private key
openssl genrsa -out "$CERTS_DIR/server.key" 4096

# Generate certificate signing request (CSR)
openssl req -new \
    -key "$CERTS_DIR/server.key" \
    -out "$CERTS_DIR/server.csr" \
    -subj "/CN=qdrant/O=Zetherion AI/C=US" \
    -addext "subjectAltName=DNS:qdrant,DNS:localhost,IP:127.0.0.1"

# Generate self-signed certificate
openssl x509 -req \
    -days "$VALIDITY_DAYS" \
    -in "$CERTS_DIR/server.csr" \
    -signkey "$CERTS_DIR/server.key" \
    -out "$CERTS_DIR/server.crt" \
    -extfile <(printf "subjectAltName=DNS:qdrant,DNS:localhost,IP:127.0.0.1")

# Clean up CSR
rm -f "$CERTS_DIR/server.csr"

# Set appropriate permissions
chmod 600 "$CERTS_DIR/server.key"
chmod 644 "$CERTS_DIR/server.crt"

echo ""
echo "Certificates generated successfully:"
echo "  Certificate: $CERTS_DIR/server.crt"
echo "  Private Key: $CERTS_DIR/server.key"
echo ""
echo "Certificate details:"
openssl x509 -in "$CERTS_DIR/server.crt" -text -noout | grep -E "(Subject:|Not Before:|Not After:|DNS:|IP:)"
