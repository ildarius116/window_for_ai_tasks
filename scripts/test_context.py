#!/usr/bin/env python3
"""Test conversation context handling in auto-router."""

import json
import os
import sys
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOKEN = os.environ.get("OWUI_TOKEN", "")
BASE = "http://localhost:3000"
MODEL = "mws_auto_router.mws-auto"


def send_chat(messages: list, timeout: int = 120) -> str:
    url = f"{BASE}/api/chat/completions"
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return json.dumps(body, ensure_ascii=False)[:2000]
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}"
    except Exception as e:
        return f"ERROR: {e}"


def main():
    if not TOKEN:
        print("ERROR: Set OWUI_TOKEN")
        sys.exit(1)

    print("=" * 60)
    print("  Context Test: follow-up references to previous messages")
    print("=" * 60)

    # Test 1: EN request, then "now in Russian"
    print("\n--- Test 1: EN -> 'now in Russian' ---")
    msg1 = "What's on https://httpbin.org/html ?"
    print(f"  User: {msg1}")
    resp1 = send_chat([{"role": "user", "content": msg1}])
    print(f"  Assistant: {resp1[:200]}...")

    msg2 = "А теперь предыдущий ответ на русском"
    print(f"\n  User: {msg2}")
    resp2 = send_chat([
        {"role": "user", "content": msg1},
        {"role": "assistant", "content": resp1},
        {"role": "user", "content": msg2},
    ])
    print(f"  Assistant: {resp2[:500]}...")

    # Check: response should be in Russian and reference httpbin content
    has_russian = any(c in resp2 for c in "абвгдежзиклмнопрстуфхцчшщэюя")
    has_reference = any(w in resp2.lower() for w in ["httpbin", "moby", "html", "perth", "кузнец", "страниц"])
    print(f"\n  Check - Russian text: {'PASS' if has_russian else 'FAIL'}")
    print(f"  Check - References previous: {'PASS' if has_reference else 'FAIL'}")
    print(f"  Overall: {'PASS' if has_russian and has_reference else 'FAIL'}")

    # Test 2: Simple follow-up
    print("\n--- Test 2: Follow-up question ---")
    msg3 = "Найди в интернете свежие новости про SpaceX"
    print(f"  User: {msg3}")
    resp3 = send_chat([{"role": "user", "content": msg3}])
    print(f"  Assistant: {resp3[:200]}...")

    msg4 = "Расскажи подробнее про первую новость"
    print(f"\n  User: {msg4}")
    resp4 = send_chat([
        {"role": "user", "content": msg3},
        {"role": "assistant", "content": resp3},
        {"role": "user", "content": msg4},
    ])
    print(f"  Assistant: {resp4[:500]}...")

    has_detail = len(resp4) > 100
    not_generic = "уточни" not in resp4.lower() and "какую" not in resp4.lower()
    print(f"\n  Check - Has detail: {'PASS' if has_detail else 'FAIL'}")
    print(f"  Check - Not generic: {'PASS' if not_generic else 'FAIL'}")
    print(f"  Overall: {'PASS' if has_detail and not_generic else 'FAIL'}")

    # Test 3: "What was the first message?"
    print("\n--- Test 3: 'What was the first message in this session?' ---")
    msg5 = "а о чем было первое сообщение в этой сессии?"
    print(f"  User (after msgs 1-4): {msg5}")
    resp5 = send_chat([
        {"role": "user", "content": msg1},
        {"role": "assistant", "content": resp1},
        {"role": "user", "content": msg2},
        {"role": "assistant", "content": resp2},
        {"role": "user", "content": msg5},
    ])
    print(f"  Assistant: {resp5[:500]}...")

    # Should reference httpbin or the first question
    refs_first = any(w in resp5.lower() for w in ["httpbin", "html", "what's on", "ссылк", "сайт"])
    print(f"\n  Check - References first message: {'PASS' if refs_first else 'FAIL'}")
    print(f"  Overall: {'PASS' if refs_first else 'FAIL'}")


if __name__ == "__main__":
    main()
