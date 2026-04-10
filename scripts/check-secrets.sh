#!/usr/bin/env bash
# check-secrets.sh — Validate .env secrets and check for accidental exposure
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

errors=0
warnings=0

echo "=== MWS GPT Platform — Secrets Check ==="
echo ""

# --- 1. Check .env exists ---
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}[ERROR] .env file not found at $ENV_FILE${NC}"
    echo "  Run: cp .env.example .env and fill in the values"
    exit 1
fi

# --- 2. Required keys must be non-empty ---
REQUIRED_KEYS=(
    ANTHROPIC_API_KEY
    OPENAI_API_KEY
    LITELLM_MASTER_KEY
    OPENWEBUI_SECRET_KEY
    POSTGRES_PASSWORD
    LANGFUSE_NEXTAUTH_SECRET
    LANGFUSE_SALT
)

echo "--- Checking required .env keys ---"
for key in "${REQUIRED_KEYS[@]}"; do
    value=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
    if [ -z "$value" ]; then
        echo -e "${RED}[ERROR] $key is missing or empty${NC}"
        ((errors++))
    else
        echo -e "${GREEN}[OK]    $key is set${NC}"
    fi
done

# Optional but recommended keys
OPTIONAL_KEYS=(
    OPENROUTER_API_KEY
    QWEN_API_KEY
    LANGFUSE_PUBLIC_KEY
    LANGFUSE_SECRET_KEY
    GRAFANA_ADMIN_PASSWORD
)

echo ""
echo "--- Checking optional .env keys ---"
for key in "${OPTIONAL_KEYS[@]}"; do
    value=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
    if [ -z "$value" ]; then
        echo -e "${YELLOW}[WARN]  $key is not set (optional)${NC}"
        ((warnings++))
    else
        echo -e "${GREEN}[OK]    $key is set${NC}"
    fi
done

# --- 3. Check for weak/default passwords ---
echo ""
echo "--- Checking for weak/default passwords ---"

pg_pass=$(grep "^POSTGRES_PASSWORD=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
if [ -n "$pg_pass" ] && [ ${#pg_pass} -lt 16 ]; then
    echo -e "${YELLOW}[WARN]  POSTGRES_PASSWORD is short (${#pg_pass} chars). Recommend 16+ chars.${NC}"
    ((warnings++))
fi

grafana_pass=$(grep "^GRAFANA_ADMIN_PASSWORD=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
if [ -z "$grafana_pass" ] || [ "$grafana_pass" = "admin" ]; then
    echo -e "${YELLOW}[WARN]  GRAFANA_ADMIN_PASSWORD is default ('admin'). Change it for production.${NC}"
    ((warnings++))
fi

weak_patterns=("password" "123456" "admin" "secret" "changeme" "default")
for key in POSTGRES_PASSWORD OPENWEBUI_SECRET_KEY LITELLM_MASTER_KEY; do
    value=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
    if [ -n "$value" ]; then
        lower_value=$(echo "$value" | tr '[:upper:]' '[:lower:]')
        for pattern in "${weak_patterns[@]}"; do
            if [ "$lower_value" = "$pattern" ]; then
                echo -e "${RED}[ERROR] $key has a weak/default value ('$pattern')${NC}"
                ((errors++))
                break
            fi
        done
    fi
done

# --- 4. Check no secrets in tracked files ---
echo ""
echo "--- Scanning tracked files for leaked secrets ---"

cd "$PROJECT_ROOT"

# Patterns that look like real API keys/secrets (not placeholder references like ${VAR})
SECRET_PATTERNS=(
    'sk-ant-api[a-zA-Z0-9_-]{20,}'
    'sk-or-v1-[a-zA-Z0-9]{20,}'
    'sk-proj-[a-zA-Z0-9_-]{20,}'
    'sk-lf-[a-zA-Z0-9-]{20,}'
    'pk-lf-[a-zA-Z0-9-]{20,}'
)

leaked=0
for pattern in "${SECRET_PATTERNS[@]}"; do
    # Search only git-tracked files, exclude .env and this script itself
    matches=$(git grep -l -E "$pattern" -- ':!.env' ':!.env.example' ':!scripts/check-secrets.sh' 2>/dev/null || true)
    if [ -n "$matches" ]; then
        echo -e "${RED}[ERROR] Secret pattern '$pattern' found in tracked files:${NC}"
        echo "$matches" | while read -r file; do
            echo "         - $file"
        done
        ((leaked++))
    fi
done

if [ "$leaked" -eq 0 ]; then
    echo -e "${GREEN}[OK]    No secret patterns found in tracked files${NC}"
fi
errors=$((errors + leaked))

# --- 5. Check .env is in .gitignore ---
echo ""
echo "--- Checking .gitignore ---"
if git check-ignore -q .env 2>/dev/null; then
    echo -e "${GREEN}[OK]    .env is in .gitignore${NC}"
else
    echo -e "${RED}[ERROR] .env is NOT in .gitignore — secrets may be committed!${NC}"
    ((errors++))
fi

# --- Summary ---
echo ""
echo "=== Summary ==="
echo -e "Errors:   $errors"
echo -e "Warnings: $warnings"

if [ "$errors" -gt 0 ]; then
    echo -e "${RED}Fix the errors above before deploying.${NC}"
    exit 1
else
    echo -e "${GREEN}All critical checks passed.${NC}"
    exit 0
fi
