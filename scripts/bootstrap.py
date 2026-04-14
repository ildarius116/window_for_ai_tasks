"""
MWS GPT Platform — one-shot bootstrap sidecar.

Runs as a small `python:3.11-slim` container on every `docker compose up`.
Idempotent and safe to re-run.

Flow:
    1. Wait for PostgreSQL to accept connections.
    2. Wait for OpenWebUI's migrations to create the `function` table.
    3. Poll the `user` table until the first user exists — this is the admin
       account that the operator creates manually on first visit to
       http://localhost:3000. The first signup becomes admin by OpenWebUI's
       default flow, so we don't pre-create anything.
    4. UPSERT the Pipe/Filter function source files from /pipelines into the
       `function` table with that user as owner, `is_active=TRUE`, `is_global=TRUE`.
       From that moment they appear in the model dropdown / filter list.
    5. Exit 0.

Design:
    - No HTTP calls to OpenWebUI — avoids needing an API token.
    - Pure DB seed — works even if the operator never sets OWUI_ADMIN_TOKEN.
    - Resilient to schema variants: unknown columns are not referenced;
      column list is discovered at runtime.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import sys
import time
from typing import Any

import psycopg2
from psycopg2.extras import Json

SECRETS_DIR = pathlib.Path(os.environ.get("SECRETS_DIR", "/secrets"))
HOST_ENV_FILE = pathlib.Path(os.environ.get("HOST_ENV_FILE", "/host/.env"))

PGHOST = os.environ.get("PGHOST", "postgres")
PGPORT = int(os.environ.get("PGPORT", "5432"))
PGUSER = os.environ.get("PGUSER", "mws")
PGPASSWORD = os.environ.get("PGPASSWORD", "")
PGDATABASE = os.environ.get("PGDATABASE", "openwebui")

PIPELINES_DIR = pathlib.Path(os.environ.get("PIPELINES_DIR", "/pipelines"))

USER_POLL_HINT_EVERY = 12  # print a hint every N polls (~1 min at 5s)


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def connect():
    for attempt in range(60):
        try:
            conn = psycopg2.connect(
                host=PGHOST,
                port=PGPORT,
                user=PGUSER,
                password=PGPASSWORD,
                dbname=PGDATABASE,
                connect_timeout=5,
            )
            conn.autocommit = True
            return conn
        except Exception as e:
            log(f"waiting for postgres ({attempt + 1}/60): {e}")
            time.sleep(3)
    raise SystemExit("postgres never became reachable")


def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def wait_for_table(cur, name: str, timeout_s: int = 900) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if table_exists(cur, name):
            return
        time.sleep(3)
    raise SystemExit(f"table '{name}' never appeared in {PGDATABASE}")


def wait_for_first_user(cur) -> str:
    """Block until at least one row exists in the user table. Returns its id."""
    log(
        "waiting for first user signup at http://localhost:3000 "
        "(that account becomes admin automatically)…"
    )
    polls = 0
    while True:
        try:
            cur.execute('SELECT id FROM "user" ORDER BY created_at ASC LIMIT 1')
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
        except Exception as e:
            log(f"user-poll error (will retry): {e}")
        polls += 1
        if polls % USER_POLL_HINT_EVERY == 0:
            log("still waiting for first user signup…")
        time.sleep(5)


def get_columns(cur, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def seed_function(
    cur,
    user_id: str,
    file_path: pathlib.Path,
    fn_id: str,
    fn_name: str,
    fn_desc: str,
    fn_type: str,
) -> None:
    if not file_path.is_file():
        log(f"⚠ {file_path} not found, skipping")
        return

    content = file_path.read_text(encoding="utf-8")
    now_s = int(time.time())

    cols = get_columns(cur, "function")
    if not cols:
        log("⚠ function table has no columns? aborting this seed")
        return

    # Base row. Only include columns that actually exist in the target schema.
    row: dict[str, Any] = {
        "id": fn_id,
        "user_id": user_id,
        "name": fn_name,
        "type": fn_type,
        "content": content,
        "meta": Json({"description": fn_desc, "manifest": {}}),
        "valves": Json({}),
        "is_active": True,
        "is_global": True,
        "created_at": now_s,
        "updated_at": now_s,
    }
    row = {k: v for k, v in row.items() if k in cols}

    col_list = list(row.keys())
    placeholders = ", ".join(["%s"] * len(col_list))
    col_sql = ", ".join(f'"{c}"' for c in col_list)

    update_cols = [
        c
        for c in col_list
        if c not in {"id", "user_id", "created_at"}
    ]
    update_sql = ", ".join(f'"{c}"=EXCLUDED."{c}"' for c in update_cols)

    sql = (
        f'INSERT INTO "function" ({col_sql}) VALUES ({placeholders}) '
        f'ON CONFLICT (id) DO UPDATE SET {update_sql}'
    )
    cur.execute(sql, [row[c] for c in col_list])
    log(f"✓ seeded {fn_id} ({fn_type}, is_active=true, is_global=true)")


def ensure_admin_api_token(cur, user_id: str) -> None:
    """Generate (or reuse) an OpenWebUI API key for the admin and publish it
    to (a) the shared /secrets file that the openwebui container mounts
    read-only, and (b) the host .env file so it survives recreation.

    OpenWebUI stores API keys in a dedicated `api_key` table with columns
    (id, user_id, key, data, expires_at, last_used_at, created_at, updated_at).
    It authenticates `Authorization: Bearer <token>` by matching `api_key.key`
    against the token, so INSERTing a row with our generated value is enough
    to make the token valid — no HTTP dance needed.
    """
    if not table_exists(cur, "api_key"):
        log("⚠ api_key table missing — skipping admin token provisioning")
        return

    # Reuse any existing bootstrap-provisioned key first.
    cur.execute(
        "SELECT key FROM api_key WHERE user_id=%s AND id=%s",
        (user_id, "mws_bootstrap_admin"),
    )
    row = cur.fetchone()
    if row and row[0]:
        token = row[0]
        log("✓ reusing existing admin api_key from DB")
    else:
        token = "sk-" + secrets.token_hex(32)
        now_s = int(time.time())
        cols = get_columns(cur, "api_key")
        row_data: dict[str, Any] = {
            "id": "mws_bootstrap_admin",
            "user_id": user_id,
            "key": token,
            "data": Json({"name": "mws-bootstrap-admin"}),
            "created_at": now_s,
            "updated_at": now_s,
        }
        row_data = {k: v for k, v in row_data.items() if k in cols}
        col_list = list(row_data.keys())
        placeholders = ", ".join(["%s"] * len(col_list))
        col_sql = ", ".join(f'"{c}"' for c in col_list)
        cur.execute(
            f'INSERT INTO api_key ({col_sql}) VALUES ({placeholders}) '
            f'ON CONFLICT (id) DO UPDATE SET "updated_at"=EXCLUDED."updated_at"',
            [row_data[c] for c in col_list],
        )
        log("✓ generated new admin api_key and stored in DB")

    # Publish to shared file (openwebui reads this at pipe call time).
    try:
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        token_file = SECRETS_DIR / "owui_admin_token"
        token_file.write_text(token, encoding="utf-8")
        try:
            token_file.chmod(0o600)
        except Exception:
            pass
        log(f"✓ wrote token to {token_file}")
    except Exception as e:
        log(f"⚠ could not write {SECRETS_DIR}/owui_admin_token: {e}")

    # Persist to host .env so next `docker compose up` picks it up via env.
    try:
        update_host_env("OWUI_ADMIN_TOKEN", token)
    except Exception as e:
        log(f"⚠ could not update {HOST_ENV_FILE}: {e}")


def update_host_env(key: str, value: str) -> None:
    """Idempotently set KEY=value in the host .env file. Only overwrites if
    the current value is empty — never clobbers an operator-provided token."""
    if not HOST_ENV_FILE.exists():
        log(f"⚠ {HOST_ENV_FILE} not mounted, skipping .env update")
        return

    lines = HOST_ENV_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    changed = False
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}="):
            found = True
            # Extract current value, stripping inline comment.
            rhs = line.split("=", 1)[1]
            comment = ""
            if "#" in rhs:
                idx = rhs.index("#")
                comment = "  " + rhs[idx:]
                rhs = rhs[:idx]
            current = rhs.strip()
            if current:
                out.append(line)  # keep operator value intact
            else:
                out.append(f"{key}={value}{comment}")
                changed = True
        else:
            out.append(line)

    if not found:
        out.append(f"{key}={value}")
        changed = True

    if changed:
        HOST_ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
        log(f"✓ {HOST_ENV_FILE.name}: set {key}")


def enable_api_keys_in_config(cur) -> None:
    """Ensure auth.enable_api_keys=True in the persistent config table.

    OpenWebUI reads ENABLE_API_KEYS env var only on first boot; after that,
    the value is persisted in `config.data.auth.enable_api_keys` and the env
    var is ignored. The bootstrap-provisioned admin api_key is worthless
    unless this flag is true, because /api/v1/files/ rejects api-key bearer
    auth otherwise (403 "Use of API key is not enabled in the environment").
    """
    if not table_exists(cur, "config"):
        log("⚠ config table missing — cannot enable api keys")
        return
    cur.execute("SELECT id, data FROM config ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    import json as _json
    if row:
        config_id, data = row
        if isinstance(data, str):
            data = _json.loads(data)
        data = data or {}
        auth = data.get("auth") or {}
        if auth.get("enable_api_keys") is True:
            log("✓ api_keys already enabled in config")
            return
        auth["enable_api_keys"] = True
        # Keep endpoint restrictions off so file upload works.
        auth["enable_api_keys_endpoint_restrictions"] = False
        data["auth"] = auth
        cur.execute("UPDATE config SET data=%s WHERE id=%s", (Json(data), config_id))
        log("✓ enabled auth.enable_api_keys in config")
    else:
        data = {"version": 0, "auth": {"enable_api_keys": True, "enable_api_keys_endpoint_restrictions": False}}
        cur.execute(
            "INSERT INTO config (data, version, created_at) VALUES (%s, 0, now())",
            (Json(data),),
        )
        log("✓ created config row with api_keys enabled")


def main() -> int:
    log(
        f"connecting to postgres://{PGUSER}@{PGHOST}:{PGPORT}/{PGDATABASE}"
    )
    conn = connect()

    with conn.cursor() as cur:
        wait_for_table(cur, "user")
        wait_for_table(cur, "function")
        enable_api_keys_in_config(cur)
        user_id = wait_for_first_user(cur)
        log(f"✓ first user detected: id={user_id}")

        # Mirror compose-resolved secrets into .env so the file stays the
        # single source of truth. Values come from compose defaults on first
        # boot or from the operator's existing .env on subsequent boots —
        # either way bootstrap writes them through. update_host_env() never
        # clobbers operator-provided values (only empty lines are filled).
        mirrored_keys = (
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "LITELLM_MASTER_KEY",
            "OPENWEBUI_SECRET_KEY",
            "POSTGRES_PASSWORD",
            "LANGFUSE_NEXTAUTH_SECRET",
            "LANGFUSE_SALT",
        )
        for env_key in mirrored_keys:
            val = os.environ.get(env_key, "").strip()
            if val:
                try:
                    update_host_env(env_key, val)
                except Exception as e:
                    log(f"⚠ could not update {HOST_ENV_FILE} for {env_key}: {e}")

        ensure_admin_api_token(cur, user_id)

        seed_function(
            cur,
            user_id,
            PIPELINES_DIR / "auto_router_function.py",
            fn_id="mws_auto_router",
            fn_name="MWS GPT Auto Router",
            fn_desc="Auto-routes requests to the best MWS model via parallel subagents",
            fn_type="pipe",
        )
        seed_function(
            cur,
            user_id,
            PIPELINES_DIR / "memory_function.py",
            fn_id="mws_memory",
            fn_name="MWS Memory",
            fn_desc="Injects and extracts long-term user memories",
            fn_type="filter",
        )
        seed_function(
            cur,
            user_id,
            PIPELINES_DIR / "image_gen_function.py",
            fn_id="mws_image_gen",
            fn_name="MWS Image Generation",
            fn_desc="Exposes mws/qwen-image* as virtual chat models (routes to /v1/images/generations).",
            fn_type="pipe",
        )

    log("✅ done")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        log(f"❌ fatal: {e}")
        sys.exit(1)
