#!/usr/bin/env python3
"""Deploy auto_router_function.py via OpenWebUI API."""
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = os.environ.get("OWUI_TOKEN", "")
BASE = "http://localhost:3000"

if not TOKEN:
    print("ERROR: Set OWUI_TOKEN")
    sys.exit(1)

with open("pipelines/auto_router_function.py", "r", encoding="utf-8") as f:
    content = f.read()

payload = json.dumps({
    "id": "mws_auto_router",
    "name": "MWS GPT Auto Router",
    "meta": {"description": "Auto-router"},
    "content": content,
}).encode("utf-8")

req = urllib.request.Request(
    f"{BASE}/api/v1/functions/id/mws_auto_router/update",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")[:300]
        print(f"Status: {resp.status}")
        print(body)
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:300]}")
except Exception as e:
    print(f"Error: {e}")
