#!/usr/bin/env bash
# ensure-env.sh — idempotently populate .env with secure random secrets
# before `docker compose up`. Never overwrites operator-provided values.
# Run automatically from `make up` / `make setup` / `make prod`.
#
# Generates on first run (values are the ones compose will see at up-time):
#   - LITELLM_MASTER_KEY
#   - OPENWEBUI_SECRET_KEY
#   - POSTGRES_PASSWORD
#   - LANGFUSE_NEXTAUTH_SECRET
#   - LANGFUSE_SALT
#
# NOT generated (set elsewhere or manual):
#   - MWS_GPT_API_KEY (operator must provide — upstream key)
#   - OWUI_ADMIN_TOKEN (bootstrap sidecar writes it after first admin signup)
#   - LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY (bootstrap mirrors compose defaults)

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "[ensure-env] created .env from .env.example"
  else
    : > "$ENV_FILE"
    echo "[ensure-env] created empty .env"
  fi
fi

# Generate-if-empty. Uses openssl for cryptographically secure randoms.
ensure_key() {
  local key="$1"
  local value="$2"
  # Extract current value, stripping inline comment.
  local current
  current="$(grep -E "^${key}=" "$ENV_FILE" | head -n1 | sed -E "s/^${key}=//" | sed -E 's/[[:space:]]*#.*$//' | sed -E 's/[[:space:]]+$//' || true)"
  if [[ -n "$current" ]]; then
    return 0
  fi
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # Replace empty line in place, preserving any inline comment.
    # Use a portable sed (GNU / BSD).
    if sed --version >/dev/null 2>&1; then
      sed -i -E "s|^(${key}=)([[:space:]]*)(#.*)?$|\1${value}\2\3|" "$ENV_FILE"
    else
      sed -i '' -E "s|^(${key}=)([[:space:]]*)(#.*)?$|\1${value}\2\3|" "$ENV_FILE"
    fi
  else
    printf "%s=%s\n" "$key" "$value" >> "$ENV_FILE"
  fi
  echo "[ensure-env] generated $key"
}

ensure_key LITELLM_MASTER_KEY        "$(openssl rand -hex 32)"
ensure_key OPENWEBUI_SECRET_KEY      "$(openssl rand -hex 32)"
ensure_key POSTGRES_PASSWORD         "$(openssl rand -hex 16)"
ensure_key LANGFUSE_NEXTAUTH_SECRET  "$(openssl rand -hex 32)"
ensure_key LANGFUSE_SALT             "$(openssl rand -hex 32)"

# Sanity check: MWS_GPT_API_KEY is required and cannot be auto-generated.
mws_key="$(grep -E '^MWS_GPT_API_KEY=' "$ENV_FILE" | head -n1 | sed -E 's/^MWS_GPT_API_KEY=//' | sed -E 's/[[:space:]]*#.*$//' | sed -E 's/[[:space:]]+$//' || true)"
if [[ -z "$mws_key" ]]; then
  echo "[ensure-env] ⚠ MWS_GPT_API_KEY is empty in .env — set it before services will work" >&2
fi

echo "[ensure-env] ✅ .env ready"
