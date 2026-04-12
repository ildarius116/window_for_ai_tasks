# Task: phase-10-10 — Fix embeddings `encoding_format` upstream incompatibility

## Severity
BLOCKER for phase-10 (memory episodes). Also blocks any memory-service code path
that computes embeddings: `POST /memories/search`, `POST /episodes`,
`POST /episodes/recall`, and the inlet-side memory recall.

## Symptom
All `/v1/embeddings` calls through LiteLLM to MWS GPT upstream return HTTP 400:

```
litellm.BadRequestError: OpenAIException - Error code: 400 -
{'error': {'message': "mwsgpt.BadRequestError: OpenAIException -
 Failed to parse the request body as JSON:
 encoding_format: expected value at line 1 column 67.
 Received Model Group=bge-m3"}}
```

Applies to `mws/bge-m3`, `mws/bge-gemma2`, `mws/qwen3-embedding` — i.e. the
failure is in LiteLLM's request serialization, not the alias.

## Root cause
LiteLLM, when calling an `openai/...`-style embeddings provider, serializes
`encoding_format` into the upstream JSON with a value that MWS GPT API cannot
parse. Empirically, if the client sends `encoding_format: "float"` explicitly
in the outbound request, LiteLLM forwards the accepted literal and upstream
returns **HTTP 200** with a valid `data[0].embedding` of length 1024:

```bash
# Reproducer from inside the memory-service container:
python -c "
import httpx, os
r = httpx.post('http://litellm:4000/v1/embeddings',
    json={'model':'mws/bge-m3','input':'test','encoding_format':'float'},
    headers={'Authorization': f'Bearer {os.environ[\"LITELLM_API_KEY\"]}'},
    timeout=30)
print(r.status_code, r.text[:200])
"
# -> 200 {"model":"mws/bge-m3","data":[{"embedding":[0.00143...]}...
```

Without `encoding_format` in the outbound body, LiteLLM (or its openai client)
defaults to something MWS GPT rejects.

## Fix (minimal — 1 file)

In `memory-service/app/embedding.py`, add `encoding_format: "float"` to the
outbound JSON body:

```python
json={
    "model": EMBEDDING_MODEL,
    "input": text,
    "encoding_format": "float",   # <-- add
},
```

No other change needed. After rebuild:

```bash
docker compose build memory-service && docker compose up -d memory-service
docker compose exec memory-service python -c \
  "import asyncio; from app.embedding import get_embedding; \
   v=asyncio.run(get_embedding('привет мир')); print(len(v), v[:3])"
# -> 1024 [0.00143..., 0.02266..., -0.02363...]
```

## Follow-up (optional, v2)
Investigate whether LiteLLM should be patched globally so `drop_params: true`
also strips/fixes `encoding_format` for the `openai/...` provider when talking
to MWS GPT. This would remove the need for the workaround in every client.
Scope: `litellm/config.yaml` under the embeddings entries, e.g.

```yaml
- model_name: mws/bge-m3
  litellm_params:
    model: openai/bge-m3
    api_key: os.environ/MWS_GPT_API_KEY
    api_base: https://api.gpt.mws.ru/v1
    encoding_format: "float"   # force a value upstream accepts
```

## Verification
After the fix, re-run phase-10-9 scenarios S1–S7 (S6 still optional).

## Dependencies
None — self-contained 1-line code change.
