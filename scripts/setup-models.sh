#!/bin/bash
# Run after first `docker compose up` and admin account creation
# Usage: ./scripts/setup-models.sh

OPENWEBUI_URL="${OPENWEBUI_URL:-http://localhost:3000}"
API_KEY="${OPENWEBUI_API_KEY}"  # Get from OpenWebUI Settings > Account > API Keys

if [ -z "$API_KEY" ]; then
  echo "Error: Set OPENWEBUI_API_KEY environment variable first"
  echo "Get it from: OpenWebUI Settings > Account > API Keys"
  exit 1
fi

echo "Configuring MWS GPT models..."

# Update model descriptions via OpenWebUI API
# Note: Exact API endpoint may vary by OpenWebUI version
# Check /api/models for available endpoints

curl -s -X POST "$OPENWEBUI_URL/api/models" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "mws/sonnet",
    "name": "Sonnet 4.6",
    "meta": {
      "description": "Claude Sonnet 4.6 — быстрый, общие задачи, код, объяснения",
      "capabilities": {
        "vision": false
      }
    }
  }' && echo " -> mws/sonnet configured"

curl -s -X POST "$OPENWEBUI_URL/api/models" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "mws/opus",
    "name": "Opus 4.6",
    "meta": {
      "description": "Claude Opus 4.6 — мощный, сложный анализ, длинные документы, архитектура",
      "capabilities": {
        "vision": false
      }
    }
  }' && echo " -> mws/opus configured"

echo "Done! Models configured."
