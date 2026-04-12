#!/usr/bin/env python3
"""Group D smoke tests — Web integrations via auto-router."""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOKEN = os.environ.get("OWUI_TOKEN", "")
BASE = "http://localhost:3000"
MODEL = "mws_auto_router.mws-auto"

TESTS = [
    {
        "id": "D1",
        "input": "Найди в интернете свежие новости про SpaceX",
        "expect_sa": "sa_web_search",
        "expect_model": "mws/kimi-k2",
        "checks": ["DuckDuckGo", "citations [1][2][3]"],
    },
    {
        "id": "D2",
        "input": "Поищи в интернете цену на Tesla Model Y в России",
        "expect_sa": "sa_web_search",
        "expect_model": "mws/kimi-k2",
        "checks": ["web_search branch"],
    },
    {
        "id": "D3",
        "input": "Перескажи https://ru.wikipedia.org/wiki/Python",
        "expect_sa": "sa_web_fetch",
        "expect_model": "mws/llama-3.1-8b",
        "checks": ["URL extractor", "summarize"],
    },
    {
        "id": "D4",
        "input": "What's on https://httpbin.org/html ?",
        "expect_sa": "sa_web_fetch",
        "expect_model": "mws/llama-3.1-8b",
        "checks": ["EN branch"],
    },
]


def send_chat(message: str, timeout: int = 120) -> str:
    """Send a non-streaming chat request to OpenWebUI and return full response text."""
    url = f"{BASE}/api/chat/completions"
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            # OpenWebUI returns OpenAI-compatible format
            choices = body.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return json.dumps(body, ensure_ascii=False)[:2000]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:1000]
        return f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return f"ERROR: {e}"


def analyze_routing_block(response: str) -> dict:
    """Extract routing info from the response's routing block."""
    info = {"subagents": [], "models": [], "raw_block": ""}
    # The auto-router emits a routing block like:
    # 🎯 **Routing:** sa_web_search (mws/kimi-k2)
    for line in response.split("\n"):
        line_lower = line.lower()
        if "routing" in line_lower or "sa_" in line_lower:
            info["raw_block"] += line + "\n"
            # Extract sa_* names
            import re
            for m in re.finditer(r"(sa_\w+)", line):
                info["subagents"].append(m.group(1))
            # Extract model names
            for m in re.finditer(r"(mws/[\w\.\-]+)", line):
                info["models"].append(m.group(1))
    return info


def run_test(test: dict) -> dict:
    """Run a single test and return results."""
    test_id = test["id"]
    print(f"\n{'='*60}")
    print(f"  {test_id}: {test['input'][:60]}...")
    print(f"  Expected: {test['expect_sa']} / {test['expect_model']}")
    print(f"{'='*60}")

    start = time.time()
    response = send_chat(test["input"])
    elapsed = time.time() - start

    print(f"  Time: {elapsed:.1f}s")
    print(f"  Response length: {len(response)} chars")

    # Show first 500 chars of response
    preview = response[:500]
    print(f"  Preview: {preview}")
    if len(response) > 500:
        print(f"  ... ({len(response) - 500} more chars)")

    # Analyze routing
    routing = analyze_routing_block(response)

    result = {
        "id": test_id,
        "input": test["input"],
        "elapsed": round(elapsed, 1),
        "response_len": len(response),
        "routing": routing,
        "response_preview": response[:1000],
        "checks": {},
    }

    # Check 1: correct subagent routed
    # Routing block shows kind without "sa_" prefix (e.g. "web_search" not "sa_web_search")
    expect_kind = test["expect_sa"].replace("sa_", "")
    sa_ok = expect_kind in routing.get("subagents", []) or test["expect_sa"] in routing.get("subagents", [])
    if not sa_ok:
        sa_ok = expect_kind in response.lower() or test["expect_sa"] in response.lower()
    result["checks"]["correct_subagent"] = sa_ok

    # Check 2: correct model
    model_ok = test["expect_model"] in routing.get("models", [])
    if not model_ok:
        model_ok = test["expect_model"] in response
    result["checks"]["correct_model"] = model_ok

    # Check 3: response is not an error
    is_error = response.startswith("HTTP ") or response.startswith("ERROR:")
    result["checks"]["no_error"] = not is_error

    # Check 4: response has substance (>50 chars of actual content)
    result["checks"]["has_content"] = len(response) > 50

    # Check 5: test-specific checks
    if test["expect_sa"] == "sa_web_search":
        # Should have citations [1], [2], [3]
        has_citations = "[1]" in response and "[2]" in response
        result["checks"]["has_citations"] = has_citations

    if test["expect_sa"] == "sa_web_fetch":
        # Should mention the URL or its content
        has_summary = len(response) > 100
        result["checks"]["has_summary"] = has_summary

    # Overall pass/fail
    result["passed"] = all(result["checks"].values())

    status = "PASS ✓" if result["passed"] else "FAIL ✗"
    print(f"\n  Result: {status}")
    for check, val in result["checks"].items():
        mark = "✓" if val else "✗"
        print(f"    {mark} {check}")

    return result


def main():
    if not TOKEN:
        print("ERROR: Set OWUI_TOKEN environment variable")
        sys.exit(1)

    print("=" * 60)
    print("  GROUP D: Web Integrations Smoke Tests")
    print(f"  Model: {MODEL}")
    print("=" * 60)

    results = []
    for test in TESTS:
        result = run_test(test)
        results.append(result)

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"  Passed: {passed}/{total}")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  {r['id']}: {status} ({r['elapsed']}s)")
        if not r["passed"]:
            for check, val in r["checks"].items():
                if not val:
                    print(f"       FAILED: {check}")

    # Write detailed results to file
    with open("scripts/test_group_d_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Detailed results: scripts/test_group_d_results.json")

    return 0 if passed == total else 1


if __name__ == "__main__":
    main()
