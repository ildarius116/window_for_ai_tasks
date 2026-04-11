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
import sys
import time
from typing import Any

import psycopg2
from psycopg2.extras import Json

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


def main() -> int:
    log(
        f"connecting to postgres://{PGUSER}@{PGHOST}:{PGPORT}/{PGDATABASE}"
    )
    conn = connect()

    with conn.cursor() as cur:
        wait_for_table(cur, "user")
        wait_for_table(cur, "function")
        user_id = wait_for_first_user(cur)
        log(f"✓ first user detected: id={user_id}")

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
