#!/bin/bash
# Creates additional databases needed by LiteLLM and Langfuse
# Run once after postgres starts

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_USER="${POSTGRES_USER:-mws}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

echo "Creating additional databases..."

PGPASSWORD="${POSTGRES_PASSWORD}" psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -p "$POSTGRES_PORT" -d postgres <<EOF
CREATE DATABASE litellm OWNER ${POSTGRES_USER};
CREATE DATABASE langfuse OWNER ${POSTGRES_USER};
CREATE DATABASE memory OWNER ${POSTGRES_USER};
\c memory
CREATE EXTENSION IF NOT EXISTS vector;
EOF

echo "Done! Databases: openwebui, litellm, langfuse, memory"
