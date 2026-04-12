#!/usr/bin/env python3
"""Test Wikipedia fetch from inside the openwebui container."""
import subprocess, sys

code = r'''
import httpx, asyncio

async def test():
    # Test 1: browser UA with Accept headers
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as cli:
        r = await cli.get("https://ru.wikipedia.org/wiki/Python")
        print(f"Browser UA: status={r.status_code}, len={len(r.text)}")

    # Test 2: Wikipedia REST API for summaries
    headers2 = {
        "User-Agent": "MWS-GPT-Hub/1.0 (https://gpt.mws.ru; admin@mws.ru)",
    }
    async with httpx.AsyncClient(timeout=15, headers=headers2, follow_redirects=True) as cli:
        r = await cli.get("https://ru.wikipedia.org/api/rest_v1/page/summary/Python")
        print(f"REST API: status={r.status_code}")
        if r.status_code == 200:
            import json
            d = r.json()
            print(f"  Title: {d.get('title')}")
            print(f"  Extract: {d.get('extract', '')[:200]}")

asyncio.run(test())
'''

result = subprocess.run(
    ["docker", "exec", "task-repo-openwebui-1", "python3", "-c", code],
    capture_output=True, text=True, timeout=30
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:500])
