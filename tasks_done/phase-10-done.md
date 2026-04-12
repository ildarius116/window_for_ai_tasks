# Phase 10 — Persistent Conversation Memory — Done Report

**Date:** 2026-04-11
**Git commit at verification time:** `a1d8991b9677d3ca0d11610e544a3c3df6adcada`
(phase-10 implementation is in the working tree only — no commit was created
by the tester per the non-commit constraint.)
**Tester:** TesterAgent (phase-10-9 E2E, re-run after phase-10-10 blocker fix)
**Overall status:** PASS — all automated scenarios (S1-S5, S7) green after
the `encoding_format` blocker fix was applied to
`memory-service/app/embedding.py`. S6 skipped (optional, postgres is already
a host bind-mount). S8 skipped-manual (requires live UI chat).

---

## TL;DR

- All phase-10-1..10-8 code is in place: embeddings fallback removed,
  `conversation_episodes` table + indexes live in the `memory` DB,
  `POST /episodes` and `POST /episodes/recall` wired, `sa_memory_recall`
  subagent with classifier intent + `_MEMORY_RECALL_RE` short-circuit in
  `pipelines/auto_router_function.py`, `memory_function.py` outlet posts
  to `/episodes` best-effort.
- **Phase-10-10 blocker (filed and fixed mid-verification):** the
  `memory-service` embedding body was missing `encoding_format: "float"`,
  which MWS GPT upstream rejected. A one-line fix was applied, the image
  rebuilt, and the service restarted. After that, S1-S5 and S7 all pass
  from direct container-side HTTP calls.
- Semantic recall correctly ranks topics (nginx > borsch > python for
  query "сетевой прокси"). Time-window filter correctly returns only
  the back-dated nginx episode. Cross-user scoping returns `[]` as
  expected. Existing facts memory (`/memories`, `/memories/search`) is
  untouched and still functional.

---

## Scenario results

### S1 — Embeddings alive — PASS

```
$ docker compose exec memory-service python -c \
    "import asyncio; from app.embedding import get_embedding; \
     v=asyncio.run(get_embedding('привет мир')); print(len(v), v[:3])"
1024 [-0.02608354, 0.03744648, -0.030370725]
```

1024-dim float vector, no hash-fallback warnings, no `EmbeddingError`.
The phase-10-1 removal of the hash-pseudo-embedding fallback is verified
to be working correctly now that phase-10-10 has been applied.

### S2 — Episodes written (POST /episodes) — PASS

Verified via direct container-side POST (documented fallback in the task,
since OpenWebUI UI-driven end-to-end is impractical in a headless run):

```
$ docker compose exec memory-service python -c \
    "import httpx; r=httpx.post('http://localhost:8000/episodes', \
     json={'user_id':'e2e_user_1','chat_id':'e2e_chat_1', \
     'messages':[{'role':'user','content':'как настроить nginx reverse proxy с SSL?'}, \
                 {'role':'assistant','content':'В nginx.conf создайте server блок с listen 443 ssl, укажите ssl_certificate и proxy_pass на upstream.'}], \
     'message_indices':[0,1], 'turn_start_at':'2026-04-11T14:00:00Z', \
     'turn_end_at':'2026-04-11T14:05:00Z'}, timeout=120); print(r.status_code)"
200
```

DB row verified:

```
$ docker compose exec postgres psql -U mws -d memory -c \
    "select user_id, chat_id, turn_end_at, left(summary,60) from conversation_episodes \
     order by created_at desc limit 5;"
  user_id   |  chat_id   |          turn_end_at          |                             left
------------+------------+-------------------------------+------------------------------------
 e2e_user_1 | e2e_chat_3 | 2026-04-11 16:05:00+00        | Пользователь ищет рецепт приготовления борща...
 e2e_user_1 | e2e_chat_2 | 2026-04-11 15:05:00+00        | asyncio работает с помощью event loop...
 e2e_user_1 | e2e_chat_1 | 2026-01-06 20:32:33.913905+00 | Настройте сервер nginx, чтобы он слушал 443 ssl...
```

(The nginx row shows the back-dated timestamp set for S4; originally
created at 2026-04-11T14:05:00Z, updated in S4 — see below.)

Summary is meaningful Russian text produced by `generate_summary`
(chat-completions path). Schema and indexes confirmed:

```
 conversation_episodes:
   id uuid, user_id varchar(255), chat_id varchar(255),
   turn_start_at/turn_end_at timestamptz, summary text,
   message_indices json, embedding vector(1024), created_at timestamptz
 ix_episodes_embedding  ivfflat (embedding vector_cosine_ops) WITH (lists='100')
 ix_episodes_user_time  btree (user_id, turn_end_at)
```

### S3 — Semantic recall — PASS

Two additional episodes posted for `e2e_user_1` (python async, рецепт
борща), each returning HTTP 200. Recall query `"сетевой прокси"`:

```
$ docker compose exec memory-service python -c \
    "import httpx; r=httpx.post('http://localhost:8000/episodes/recall', \
     json={'user_id':'e2e_user_1','query':'сетевой прокси','limit':5}, timeout=60); \
     print(r.status_code, r.json())"
200
[
  {"chat_id":"e2e_chat_1", "summary":"... nginx ... listen 443 ssl ... proxy_pass ...",
   "score":0.5329},
  {"chat_id":"e2e_chat_3", "summary":"рецепт приготовления борща", "score":0.3876},
  {"chat_id":"e2e_chat_2", "summary":"asyncio работает с помощью event loop ...", "score":0.3677}
]
```

Nginx ranks first with a clearly higher cosine score (0.53 vs 0.39/0.37).
Ranking logic (`1 - (embedding <=> :qvec)` ordered descending) works as
designed.

### S4 — Time-window recall — PASS

Shifted the nginx episode back 95 days:

```
$ docker compose exec postgres psql -U mws -d memory -c \
    "UPDATE conversation_episodes SET turn_end_at = now() - interval '95 days', \
     turn_start_at = now() - interval '95 days' \
     WHERE summary ILIKE '%nginx%' AND user_id='e2e_user_1' RETURNING id, turn_end_at;"
                  id                  |          turn_end_at
--------------------------------------+-------------------------------
 19b45bf9-df79-4c7c-9006-66acc5680610 | 2026-01-06 20:32:33.913905+00
```

Recall with date window `[now-120d, now-60d]`:

```
$ docker compose exec memory-service python -c \
    "import httpx, datetime; now=datetime.datetime.now(datetime.timezone.utc); \
     r=httpx.post('http://localhost:8000/episodes/recall', \
     json={'user_id':'e2e_user_1','query':'что обсуждали', \
           'date_from':(now-datetime.timedelta(days=120)).isoformat(), \
           'date_to':(now-datetime.timedelta(days=60)).isoformat(), 'limit':5}, timeout=60); \
     print(r.status_code, r.json())"
200
[{"chat_id":"e2e_chat_1", "turn_end_at":"2026-01-06T20:32:33.913905Z",
  "summary":"... nginx ... listen 443 ssl ...", "score":0.2798}]
```

Only the back-dated nginx episode is returned; python/borsch (which are at
"now") are correctly filtered out by the `turn_end_at` window.

### S5 — User scoping — PASS

```
$ docker compose exec memory-service python -c \
    "import httpx; r=httpx.post('http://localhost:8000/episodes/recall', \
     json={'user_id':'e2e_user_2','query':'сетевой прокси','limit':5}, timeout=60); \
     print(r.status_code, r.json())"
200
[]
```

Empty list for a different user, same query. The `WHERE user_id = :user_id`
clause prevents cross-user leakage.

### S6 — Persistence across restart — SKIPPED (optional)

Skipped intentionally per the task's optional clause. The postgres data
directory is now a host bind-mount (`./data/postgres`, see the CLAUDE.md
migration note from 2026-04-11), so persistence of `conversation_episodes`
reduces to "the Docker bind mount works", which is already proved by the
fact that the existing `memories` table and all 12 pre-phase-10 rows
survived the `memory-service` rebuild during this verification session.
Running `docker compose down && up` in the middle of a green run is
disruptive and adds no information.

### S7 — Facts memory not broken — PASS

Baseline:

```
$ docker compose exec postgres psql -U mws -d memory -c "select count(*) from memories;"
 count
-------
    12
```

Listing pre-existing user:

```
$ docker compose exec memory-service python -c \
    "import httpx; r=httpx.get('http://localhost:8000/memories/0b23c315-978e-4a2f-bca0-ed1bad206b19'); \
     print(r.status_code, len(r.json()))"
200 12
```

Semantic search on facts (exercises `get_embedding` + pgvector on the
`memories` table):

```
$ docker compose exec memory-service python -c \
    "import httpx; r=httpx.post('http://localhost:8000/memories/search', \
     json={'user_id':'0b23c315-978e-4a2f-bca0-ed1bad206b19', \
           'query':'предпочтения','limit':3}, timeout=60); \
     print(r.status_code, r.json()[0]['content'][:60])"
200 Байкал — самое глубокое озеро в мире
```

All three surfaces of the existing facts API (`GET`, `POST /search`,
underlying `Memory` model) still return 200 and non-empty results.
`POST /memories/extract` and the inlet injection path share the same
`get_embedding` helper that now works (verified in S1), so no regression
is expected.

### S8 — Phase-9 group A/B regression — SKIPPED-manual

Cannot be automated headlessly — the classifier/routing decisions only
trigger when driven via the actual OpenWebUI chat flow. Recommended manual
re-run with the exact prompts from `tasks_done/phase-9-done.md`:

**Group A (routing / chat quality)** — expect >=6/6:

1. `"Напиши код на Python для подсчёта простых чисел до 100"` -> `kind=code`
   -> `mws/qwen3-coder`.
2. `"Привет, как дела?"` -> `kind=ru_chat` -> `mws/qwen3-235b` (lang-aware
   override, phase-9 patch #1).
3. `"Докажи, что sqrt(2) иррационально"` -> `kind=reasoner` ->
   `mws/deepseek-r1-32b` with `### Answer:` strip (phase-9 patch #2).
4. A >=1500-char pasted transcript -> `kind=long_doc` -> `mws/glm-4.6`
   (phase-9 patch #3).
5. `"Explain quantum entanglement in simple terms"` -> `kind=general` ->
   `mws/gpt-alpha`.
6. `"Tell me a joke"` -> `kind=general` -> `mws/gpt-alpha`.

**Group B (classifier incl. stubs)** — expect >=4/4:

1. Image URL in the prompt -> `kind=vision` -> `mws/cotype-pro-vl` or
   `mws/qwen2.5-vl-72b`.
2. `"Сделай презентацию про ..."` -> `kind=presentation` (v1 stub).
3. `"Проведи deep research по теме ..."` -> `kind=deep_research` (v1 stub).
4. A `http(s)://` URL alone -> `kind=web_fetch` -> `mws/llama-3.1-8b`.

**New phase-10 memory_recall prompts to add to group B:**

- `"О чём мы говорили вчера?"` -> `kind=memory_recall`,
  `time_window.from ~= now-1d` (regex short-circuit in `_MEMORY_RECALL_RE`).
- `"напомни, что мы обсуждали три месяца назад"` ->
  `kind=memory_recall`, `time_window.from ~= now-90d`.
- `"о чём был наш разговор про nginx?"` -> `kind=memory_recall`,
  no time window.

Pass threshold: >=9/10 across groups A+B as in phase-9.

---

## Phase-10-10 blocker fix (applied mid-verification)

During the initial verification pass S1 failed with HTTP 400 from MWS GPT
upstream: `"encoding_format: expected value at line 1 column 67"`. Root
cause: `memory-service/app/embedding.py` POSTed
`{"model": ..., "input": ...}` to `http://litellm:4000/v1/embeddings`, but
the MWS GPT upstream requires `encoding_format: "float"` to be present
explicitly. Fix: added `"encoding_format": "float"` to the outbound JSON
body. Image rebuilt and restarted. S1 then returned a 1024-dim vector,
and all downstream scenarios (S2-S5, S7) passed on first retry.

Task file: `tasks/phase-10-10-fix-embeddings-encoding-format.md`.

---

## Files changed in phase 10

```
 docker-compose.yml                |   1 +
 memory-service/app/config.py      |   3 +
 memory-service/app/embedding.py   |  73 ++++++----------
 memory-service/app/main.py        |  13 ++-
 memory-service/app/models.py      |  28 ++++++-
 pipelines/auto_router_function.py | 169 ++++++++++++++++++++++++++++++++------
 pipelines/memory_function.py      |  65 ++++++++++++++-
 7 files changed, 275 insertions(+), 77 deletions(-)
```

**New files:**

```
 memory-service/app/episodes.py          (generate_summary helper)
 memory-service/app/routers/episodes.py  (POST /episodes, POST /episodes/recall)
 tasks/phase-10-10-fix-embeddings-encoding-format.md
```

**Highlights confirmed in the diff and at runtime:**

- `memory-service/app/embedding.py` — phase-10-1 hash-fallback removal +
  phase-10-10 `encoding_format: "float"` fix. Upstream now returns 1024-dim
  floats; `EmbeddingError` is now the sole terminal branch on failure.
- `memory-service/app/models.py` — `ConversationEpisode` with
  `Vector(EMBEDDING_DIMENSIONS)`, composite `ix_episodes_user_time`.
  (Phase-10-2.)
- `memory-service/app/routers/episodes.py` — `POST /episodes` (calls
  `generate_summary` -> `get_embedding` -> INSERT) and `POST /episodes/recall`
  (SQL with pgvector `<=>` cosine operator + null-tolerant timestamp
  window + user-scope WHERE). Phase-10-3 + 10-4.
- `memory-service/app/main.py` — episodes router included.
- `pipelines/auto_router_function.py`:
  - `_MEMORY_RECALL_RE` for `"о чём мы говорили / напомни / last week /
    три месяца назад"` triggers.
  - `_classify_and_plan` rule short-circuit before `long_doc` / `reasoner`.
  - `memory_recall` in `_llm_classify` intents allow-list + JSON examples
    with `time_window`.
  - `_sa_memory_recall` subagent: POSTs to `{MEMORY_SERVICE_URL}/episodes/recall`
    with `user_id` from `task.metadata`, returns a `CompactResult` whose
    summary is top-K `[YYYY-MM-DD] <summary>` lines; graceful
    `error="memory_recall: no user_id in metadata"` branch. Phase-10-5/6/7.
- `pipelines/memory_function.py` — outlet posts to `/episodes` on each
  assistant turn wrapped in a try/except that degrades gracefully.
  Phase-10-8.

## Known issues / follow-ups

1. **Phase-10-10 fix must be committed** along with the rest of phase-10
   code — currently only in the working tree.
2. **S8 still needs manual UI verification** with the prompts above (~10
   minutes of live OpenWebUI chat driving). Recommend doing this before
   merging phase-10 to main.
3. **Outlet live-path verification.** The `memory_function.py` outlet
   posts to `/episodes` from inside the OpenWebUI container on each
   assistant turn. This path was not exercised by the fallback
   verification (which POSTed directly from the `memory-service`
   container). After phase-10 is merged, a short live OpenWebUI chat
   should confirm outlet writes arrive in `conversation_episodes`.
4. **Latency / ivfflat `lists=100` tuning** — not load-tested. v2 item.

## Final status

**Phase 10 implementation: COMPLETE. Automated verification: PASS.**
S1-S5 and S7 all green after phase-10-10 blocker fix. S6 intentionally
skipped (optional + host bind-mount). S8 skipped-manual. Phase 10 is
ready to be marked DONE pending (a) commit of the working-tree changes
and (b) the 10-minute manual S8 UI pass to confirm no phase-9 regression.

---

## Appendix — evidence collection commands (for reruns)

```bash
# S1
docker compose exec memory-service python -c \
  "import asyncio; from app.embedding import get_embedding; \
   v=asyncio.run(get_embedding('привет мир')); print(len(v), v[:3])"

# S2
docker compose exec memory-service python -c \
  "import httpx; r=httpx.post('http://localhost:8000/episodes', \
   json={'user_id':'e2e_user_1','chat_id':'e2e_chat_1', \
         'messages':[{'role':'user','content':'nginx reverse proxy?'}, \
                     {'role':'assistant','content':'listen 443 ssl; proxy_pass ...'}], \
         'message_indices':[0,1], \
         'turn_start_at':'2026-04-11T14:00:00Z', \
         'turn_end_at':'2026-04-11T14:05:00Z'}, timeout=120); \
   print(r.status_code, r.text[:200])"

# S3
docker compose exec memory-service python -c \
  "import httpx; r=httpx.post('http://localhost:8000/episodes/recall', \
   json={'user_id':'e2e_user_1','query':'сетевой прокси','limit':5}, timeout=60); \
   print(r.json())"

# S4
docker compose exec postgres psql -U mws -d memory -c \
  "update conversation_episodes set turn_end_at = now() - interval '95 days' \
   where summary ilike '%nginx%' and user_id='e2e_user_1' returning id;"

# S5
docker compose exec memory-service python -c \
  "import httpx; r=httpx.post('http://localhost:8000/episodes/recall', \
   json={'user_id':'e2e_user_2','query':'сетевой прокси','limit':5}); \
   print(r.status_code, r.json())"

# S7
docker compose exec postgres psql -U mws -d memory -c "select count(*) from memories;"
```
