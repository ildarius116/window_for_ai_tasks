"""E2E verification for Phase 12 attribution-checker subagent.

Runs the same way as scripts/e2e_memory_test.py:
    docker cp scripts/e2e_fact_check_test.py task-repo-openwebui-1:/tmp/fc.py
    docker cp pipelines/auto_router_function.py task-repo-openwebui-1:/tmp/auto_router_function.py
    docker exec task-repo-openwebui-1 python /tmp/fc.py

Semantics (post-pivot): we do NOT judge whether claims are true, only whether
they are traceable to the snippets of URLs the subagent actually fetched.
Verdicts: grounded (✅ — in source), partial/ungrounded/unknown (⚠️ — not in
fetched source). ❌ is gone.

Covers:
  F1  Real case "Мисс Мира 2025"           — live pipe, expects attribution to run
  F2  Messi Olympics 2012 query            — live pipe, expects details block to exist
  F3  Force trigger ("проверь факты: ...")  — even with plan=[general], check runs
  F4  "Привет, как дела?"                   — attribution SHOULD NOT run
  F5  Invalid URL injected (mocked result)  — unit-ish, asserts url_unreachable
  F6  Timeout (fact_check_timeout=0.5)      — asserts CompactResult.error=="timeout"
  F7  Regression: presentation untouched    — plan=[presentation] → skip

Scenarios F1–F4 + F7 hit the live LiteLLM → MWS GPT API path and may fail
on network issues; keep API keys in the env. F5/F6 are hermetic and always run.
"""
import asyncio
import importlib.util
import os
import sys
import types


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- Utilities ---------------------------------------------------------------

async def _run_pipe(pipe, body, user=None):
    buf: list[str] = []
    async for chunk in pipe.pipe(body, __user__=user or {"id": "e2e-fc-user"}):
        buf.append(chunk)
    return "".join(buf)


def _has_routing_verifier(text: str) -> bool:
    return "Verifiers" in text and "fact_check" in text


def _has_details_check(text: str) -> bool:
    return "Проверка источников" in text


# --- Scenarios ---------------------------------------------------------------

async def f1_miss_world(pipe):
    print("\n=== F1 — Miss World 2025 ===")
    body = {
        "model": "mws_auto_router.mws-auto",
        "messages": [
            {"role": "user", "content":
             "Найди информацию о победительнице конкурса 'Мисс мира 2025' "
             "и объясни, почему она победила"}
        ],
        "stream": False,
    }
    out = await _run_pipe(pipe, body)
    print(out[:2000])
    assert _has_routing_verifier(out), "F1: Verifiers line missing from routing block"
    assert _has_details_check(out), "F1: '✅ Проверка источников' details block missing"
    print("F1: PASS")


async def f2_messi_trap(pipe):
    print("\n=== F2 — Messi Olympics 2012 query ===")
    body = {
        "model": "mws_auto_router.mws-auto",
        "messages": [
            {"role": "user", "content":
             "На каком олимпийском турнире Лионель Месси выиграл золото в 2012 году?"}
        ],
        "stream": False,
    }
    out = await _run_pipe(pipe, body)
    print(out[:2000])
    # Attribution check: we only care that the details block exists and claims
    # are labeled. We do NOT assert the specific verdict — that depends on
    # whether the subagent fetched a source refuting 2012.
    assert _has_details_check(out), "F2: details block must exist when plan hits checkable kinds"
    print("F2: PASS")


async def f3_force_trigger(pipe):
    print("\n=== F3 — Forced trigger ===")
    body = {
        "model": "mws_auto_router.mws-auto",
        "messages": [
            {"role": "user", "content":
             "Проверь факты в этом тексте: Земля плоская, Солнце крутится вокруг неё."}
        ],
        "stream": False,
    }
    out = await _run_pipe(pipe, body)
    print(out[:2000])
    assert _has_routing_verifier(out), "F3: Verifiers line missing (force trigger)"
    # Attribution mode doesn't assert ❌ — absence of a source snippet for a
    # claim yields ⚠️ (ungrounded), not a truth verdict.
    assert _has_details_check(out), "F3: details block must exist on forced trigger"
    print("F3: PASS")


async def f4_smalltalk(pipe):
    print("\n=== F4 — Smalltalk, no fact-check ===")
    body = {
        "model": "mws_auto_router.mws-auto",
        "messages": [{"role": "user", "content": "Привет, как дела?"}],
        "stream": False,
    }
    out = await _run_pipe(pipe, body)
    print(out[:1500])
    assert not _has_routing_verifier(out), "F4: Verifiers line must be absent for smalltalk"
    assert not _has_details_check(out), "F4: details block must be absent"
    print("F4: PASS")


async def f5_invalid_url(auto_mod, pipe):
    print("\n=== F5 — Invalid URL (hermetic) ===")
    CompactResult = auto_mod.CompactResult
    DetectedInput = auto_mod.DetectedInput
    fake_results = [
        CompactResult(
            kind="web_search",
            summary=(
                "Согласно [1] в 2030 году прошёл тайный саммит на Марсе "
                "с участием Илона Маска и инопланетной делегации."
            ),
            citations=["https://nosuch-domain-q8xr5.example/mars-summit"],
        )
    ]
    detected = DetectedInput(last_user_text="что известно про саммит на Марсе?")
    fc = await pipe._sa_fact_check(fake_results, detected, detected.last_user_text)
    print("fc.summary:", fc.summary)
    print("fc.error:", fc.error)
    report = (fc.metadata or {}).get("report") or {}
    print("urls:", report.get("urls"))
    print("claims:", report.get("claims"))
    assert fc.kind == "fact_check"
    # The made-up URL must end up url_unreachable (DNS fail)
    urls = report.get("urls") or []
    assert any(u.get("status") == "url_unreachable" for u in urls), \
        "F5: expected url_unreachable status for nosuch-domain-q8xr5.example"
    print("F5: PASS")


async def f6_timeout(auto_mod, pipe):
    print("\n=== F6 — Timeout ===")
    CompactResult = auto_mod.CompactResult
    DetectedInput = auto_mod.DetectedInput
    fake_results = [
        CompactResult(
            kind="web_search",
            summary="Test claim A. Test claim B.",
            citations=["https://www.wikipedia.org/"],
        )
    ]
    detected = DetectedInput(last_user_text="smoke timeout test")
    original = pipe.valves.fact_check_timeout
    pipe.valves.fact_check_timeout = 0.01
    try:
        fc = await pipe._sa_fact_check(fake_results, detected, detected.last_user_text)
    finally:
        pipe.valves.fact_check_timeout = original
    print("fc.summary:", fc.summary, "| fc.error:", fc.error)
    assert fc.error == "timeout", f"F6: expected error='timeout', got {fc.error!r}"
    print("F6: PASS")


async def f7_presentation_regression(pipe):
    print("\n=== F7 — Presentation not touched ===")
    body = {
        "model": "mws_auto_router.mws-auto",
        "messages": [
            {"role": "user", "content": "Сделай презентацию про Python async/await на 5 слайдов"}
        ],
        "stream": False,
    }
    out = await _run_pipe(pipe, body)
    # Presentation intent is NOT in _CHECKABLE_KINDS → no fact-check.
    assert not _has_routing_verifier(out), \
        "F7: presentation plan should NOT trigger fact-check"
    assert not _has_details_check(out), \
        "F7: details block must be absent for presentation-only plan"
    print("F7: PASS")


# --- Main --------------------------------------------------------------------

async def main():
    auto_path = os.environ.get(
        "AUTO_ROUTER_PATH", "/tmp/auto_router_function.py"
    )
    auto_mod = _load(auto_path, "auto_router_function")
    pipe = auto_mod.Pipe()

    # hermetic first (don't need network)
    await f5_invalid_url(auto_mod, pipe)
    await f6_timeout(auto_mod, pipe)

    # live — require LITELLM_MASTER_KEY + MWS_GPT_API_KEY in env
    if os.environ.get("SKIP_LIVE") == "1":
        print("\nSKIP_LIVE=1 — skipping F1/F2/F3/F4/F7")
        return
    await f4_smalltalk(pipe)          # cheap baseline
    await f7_presentation_regression(pipe)
    await f3_force_trigger(pipe)
    await f1_miss_world(pipe)
    await f2_messi_trap(pipe)
    print("\nALL SCENARIOS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
