#!/usr/bin/env sh
set -eu

CERT_DIR=/var/lib/postgresql/certs
mkdir -p "$CERT_DIR"

install -D -o postgres -g postgres -m 0644 /etc/postgres-certs/server.crt "$CERT_DIR/server.crt"
install -D -o postgres -g postgres -m 0644 /etc/postgres-certs/ca.pem "$CERT_DIR/ca.pem"
install -D -o postgres -g postgres -m 0600 /etc/postgres-certs/server.key "$CERT_DIR/server.key"

exec docker-entrypoint.sh postgres \
  -c ssl=on \
  -c ssl_cert_file="$CERT_DIR/server.crt" \
  -c ssl_key_file="$CERT_DIR/server.key" \
  -c ssl_ca_file="$CERT_DIR/ca.pem" \
  -c hba_file=/etc/postgresql/pg_hba.conf
