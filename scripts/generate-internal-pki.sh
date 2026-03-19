#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_ROOT="${1:-"$ROOT_DIR/data/certs"}"
OPENSSL_BIN="${OPENSSL_BIN:-openssl}"
CERT_DAYS="${CERT_DAYS:-825}"
PKI_SCHEMA_VERSION="${PKI_SCHEMA_VERSION:-2}"
PKI_VERSION_FILE="$CERT_ROOT/version.txt"

if ! command -v "$OPENSSL_BIN" >/dev/null 2>&1; then
  echo "openssl is required to generate the internal PKI" >&2
  exit 1
fi

INTERNAL_DIR="$CERT_ROOT/internal"
POSTGRES_DIR="$CERT_ROOT/postgres"
QDRANT_DIR="$CERT_ROOT/qdrant"

mkdir -p "$INTERNAL_DIR" "$POSTGRES_DIR" "$QDRANT_DIR"

create_ca() {
  local cert_path="$1"
  local key_path="$2"
  local csr_path="${cert_path%.pem}.csr"
  local ext_path="${cert_path%.pem}.ext"

  if [[ -f "$cert_path" && -f "$key_path" ]]; then
    return
  fi

  {
    echo "[v3_ca]"
    echo "basicConstraints=critical,CA:TRUE,pathlen:0"
    echo "keyUsage=critical,keyCertSign,cRLSign"
    echo "subjectKeyIdentifier=hash"
    echo "authorityKeyIdentifier=keyid:always"
  } >"$ext_path"

  "$OPENSSL_BIN" req \
    -nodes \
    -newkey rsa:4096 \
    -sha256 \
    -subj "/CN=Zetherion Internal CA" \
    -keyout "$key_path" \
    -out "$csr_path"

  "$OPENSSL_BIN" x509 \
    -req \
    -in "$csr_path" \
    -signkey "$key_path" \
    -out "$cert_path" \
    -days "$CERT_DAYS" \
    -sha256 \
    -extfile "$ext_path" \
    -extensions v3_ca

  rm -f "$csr_path" "$ext_path"
  chmod 600 "$key_path"
}

make_ext_file() {
  local usage="$1"
  shift
  local ext_path="$1"
  shift
  {
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=critical,digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=$usage"
    echo "subjectKeyIdentifier=hash"
    echo "authorityKeyIdentifier=keyid,issuer"
    if (($# > 0)); then
      printf "subjectAltName="
      local first=1
      for entry in "$@"; do
        if ((first)); then
          first=0
        else
          printf ","
        fi
        if [[ "$entry" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
          printf "IP:%s" "$entry"
        else
          printf "DNS:%s" "$entry"
        fi
      done
      printf "\n"
    fi
  } >"$ext_path"
}

create_leaf() {
  local common_name="$1"
  local cert_path="$2"
  local key_path="$3"
  local usage="$4"
  shift 4
  local san_entries=("$@")

  local ca_cert="$INTERNAL_DIR/ca.pem"
  local ca_key="$INTERNAL_DIR/ca-key.pem"
  local csr_path="${cert_path%.pem}.csr"
  local ext_path="${cert_path%.pem}.ext"
  local serial_path="$INTERNAL_DIR/ca.srl"

  "$OPENSSL_BIN" req \
    -new \
    -nodes \
    -newkey rsa:4096 \
    -subj "/CN=$common_name" \
    -keyout "$key_path" \
    -out "$csr_path"

  make_ext_file "$usage" "$ext_path" "${san_entries[@]}"

  "$OPENSSL_BIN" x509 \
    -req \
    -in "$csr_path" \
    -CA "$ca_cert" \
    -CAkey "$ca_key" \
    -CAcreateserial \
    -CAserial "$serial_path" \
    -out "$cert_path" \
    -days "$CERT_DAYS" \
    -sha256 \
    -extfile "$ext_path"

  rm -f "$csr_path" "$ext_path"
  chmod 600 "$key_path"
}

copy_ca() {
  local destination_dir="$1"
  cp "$INTERNAL_DIR/ca.pem" "$destination_dir/ca.pem"
}

create_ca "$INTERNAL_DIR/ca.pem" "$INTERNAL_DIR/ca-key.pem"

copy_ca "$POSTGRES_DIR"
copy_ca "$QDRANT_DIR"

create_leaf \
  "zetherion-ai-traefik" \
  "$INTERNAL_DIR/traefik.pem" \
  "$INTERNAL_DIR/traefik-key.pem" \
  "serverAuth" \
  "zetherion-ai-traefik" "localhost" "127.0.0.1"

create_leaf \
  "zetherion-ai-api" \
  "$INTERNAL_DIR/api.pem" \
  "$INTERNAL_DIR/api-key.pem" \
  "serverAuth" \
  "zetherion-ai-api" "zetherion-ai-api-blue" "zetherion-ai-api-green"

create_leaf \
  "zetherion-ai-skills" \
  "$INTERNAL_DIR/skills.pem" \
  "$INTERNAL_DIR/skills-key.pem" \
  "serverAuth" \
  "zetherion-ai-skills" "zetherion-ai-skills-blue" "zetherion-ai-skills-green"

create_leaf \
  "zetherion-ai-cgs-gateway" \
  "$INTERNAL_DIR/cgs-gateway.pem" \
  "$INTERNAL_DIR/cgs-gateway-key.pem" \
  "serverAuth" \
  "zetherion-ai-cgs-gateway" "zetherion-ai-cgs-gateway-blue" "zetherion-ai-cgs-gateway-green"

create_leaf \
  "zetherion-ai-updater" \
  "$INTERNAL_DIR/updater.pem" \
  "$INTERNAL_DIR/updater-key.pem" \
  "serverAuth" \
  "zetherion-ai-updater"

create_leaf \
  "zetherion-ai-dev-agent" \
  "$INTERNAL_DIR/dev-agent.pem" \
  "$INTERNAL_DIR/dev-agent-key.pem" \
  "serverAuth" \
  "zetherion-ai-dev-agent"

create_leaf \
  "zetherion-internal-client" \
  "$INTERNAL_DIR/client.pem" \
  "$INTERNAL_DIR/client-key.pem" \
  "clientAuth"

create_leaf \
  "zetherion-ai-postgres" \
  "$POSTGRES_DIR/server.crt" \
  "$POSTGRES_DIR/server.key" \
  "serverAuth" \
  "postgres" "zetherion-ai-postgres" "localhost" "127.0.0.1"

create_leaf \
  "zetherion-internal-postgres-client" \
  "$POSTGRES_DIR/client.pem" \
  "$POSTGRES_DIR/client-key.pem" \
  "clientAuth"

create_leaf \
  "zetherion-ai-qdrant" \
  "$QDRANT_DIR/server.crt" \
  "$QDRANT_DIR/server.key" \
  "serverAuth" \
  "qdrant" "zetherion-ai-qdrant" "localhost" "127.0.0.1"

printf '%s\n' "$PKI_SCHEMA_VERSION" >"$PKI_VERSION_FILE"

cat <<EOF
Internal PKI generated under: $CERT_ROOT
- Internal CA: $INTERNAL_DIR/ca.pem
- Traefik server cert: $INTERNAL_DIR/traefik.pem
- API server cert: $INTERNAL_DIR/api.pem
- Skills server cert: $INTERNAL_DIR/skills.pem
- CGS gateway cert: $INTERNAL_DIR/cgs-gateway.pem
- Updater cert: $INTERNAL_DIR/updater.pem
- Dev-agent cert: $INTERNAL_DIR/dev-agent.pem
- Shared mTLS client cert: $INTERNAL_DIR/client.pem
- PostgreSQL TLS bundle: $POSTGRES_DIR
- Qdrant TLS bundle: $QDRANT_DIR
EOF
