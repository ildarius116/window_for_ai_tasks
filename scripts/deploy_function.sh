#!/usr/bin/env bash
# Deploy an OpenWebUI Pipe/Filter/Tool function source file via the Admin API.
#
# Usage:
#   OWUI_ADMIN_TOKEN=<token> bash scripts/deploy_function.sh pipelines/auto_router_function.py
#
# Env:
#   OWUI_ADMIN_TOKEN  — required. Get it from OpenWebUI → Profile → API Keys.
#   OWUI_BASE_URL     — optional, default http://localhost:3000
set -euo pipefail

FILE="${1:?usage: deploy_function.sh <pipelines/*_function.py>}"
: "${OWUI_ADMIN_TOKEN:?OWUI_ADMIN_TOKEN not set}"
: "${OWUI_BASE_URL:=http://localhost:3000}"

if [[ ! -f "$FILE" ]]; then
    echo "❌ File not found: $FILE" >&2
    exit 1
fi

BASENAME=$(basename "$FILE" _function.py)
ID="mws_${BASENAME}"

# Extract title/description from the docstring frontmatter
NAME=$(grep -oE 'title:[[:space:]]*[^[:space:]].*' "$FILE" | head -1 | sed 's/title:[[:space:]]*//' || true)
DESC=$(grep -oE 'description:[[:space:]]*[^[:space:]].*' "$FILE" | head -1 | sed 's/description:[[:space:]]*//' || true)
NAME="${NAME:-$ID}"
DESC="${DESC:-MWS GPT function}"

if ! command -v jq >/dev/null 2>&1; then
    echo "❌ jq is required (install: https://stedolan.github.io/jq/)" >&2
    exit 1
fi

PAYLOAD=$(jq -n \
    --arg id "$ID" \
    --arg name "$NAME" \
    --arg desc "$DESC" \
    --rawfile content "$FILE" \
    '{id:$id, name:$name, meta:{description:$desc}, content:$content}')

echo "→ Deploying $ID ($NAME) to ${OWUI_BASE_URL}"

HTTP_CODE=$(curl -sS -o /tmp/owui_deploy_resp.json -w "%{http_code}" \
    -X POST "${OWUI_BASE_URL}/api/v1/functions/create" \
    -H "Authorization: Bearer ${OWUI_ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" || true)

if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "201" ]]; then
    echo "✅ Created $ID"
    cat /tmp/owui_deploy_resp.json
    echo
    exit 0
fi

# If already exists, fall back to update
if grep -qi "already" /tmp/owui_deploy_resp.json 2>/dev/null; then
    echo "ℹ️  $ID already exists — updating…"
    HTTP_CODE=$(curl -sS -o /tmp/owui_deploy_resp.json -w "%{http_code}" \
        -X POST "${OWUI_BASE_URL}/api/v1/functions/id/${ID}/update" \
        -H "Authorization: Bearer ${OWUI_ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" || true)
    if [[ "$HTTP_CODE" == "200" ]]; then
        echo "✅ Updated $ID"
        cat /tmp/owui_deploy_resp.json
        echo
        exit 0
    fi
fi

echo "❌ Deploy failed (HTTP $HTTP_CODE):" >&2
cat /tmp/owui_deploy_resp.json >&2
echo >&2
exit 1
