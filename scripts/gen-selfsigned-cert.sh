#!/usr/bin/env bash
# Generate a self-signed certificate for nginx HTTPS.
# Idempotent: skips generation if cert already exists.
# Override CN via CERT_CN env var (default: localhost).

set -euo pipefail

SSL_DIR="$(dirname "$0")/../nginx/ssl"
mkdir -p "$SSL_DIR"

CERT="$SSL_DIR/fullchain.pem"
KEY="$SSL_DIR/privkey.pem"
CN="${CERT_CN:-localhost}"

if [[ -f "$CERT" && -f "$KEY" ]]; then
    echo "SSL cert already exists at $CERT — skipping generation"
    exit 0
fi

echo "Generating self-signed cert for CN=$CN ..."
# MSYS_NO_PATHCONV=1 prevents Git Bash on Windows from mangling the /CN=... subj string
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" \
    -out "$CERT" \
    -days 3650 \
    -subj "/CN=$CN" \
    -addext "subjectAltName=DNS:localhost,DNS:$CN,IP:127.0.0.1"

echo "Generated $CERT"
echo "Generated $KEY"
echo "Note: self-signed cert — browsers will show a warning. Accept it once."
