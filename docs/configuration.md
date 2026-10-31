# Configuration

The backend and workers read environment variables ([backend/app/core/config.py](../backend/app/core/config.py)).
Locally, copy `backend/.env.example` to `backend/.env` and start with
`uv run --env-file .env uvicorn app.main:app --reload --port 8000`. In Kubernetes, non-secret values are in
[deploy/k8s/base/configmap.yaml](../deploy/k8s/base/configmap.yaml) and secrets in a Secret.

With `APP_ENV=production` the backend **refuses to start** when `JWT_SECRET` is missing or shorter than
32 characters, `MINIO_SECRET_KEY` or the database password still have their development values, or
`COOKIE_SECURE` is false.

## Application

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `production`: startup checks above, JSON logs, no localhost CORS rule |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` in production, else `text` | `json`: one object per line, what Loki reads (compose and Kubernetes set it); `text`: readable in a terminal |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | comma-separated; exact origins (cookies forbid `*`) |
| `JWT_SECRET` | empty (random per start) | signs session cookies; set it, or every restart logs everyone out. **Secret** |
| `SESSION_DAYS` | `7` | how long a login lasts |
| `COOKIE_SECURE` | `false` | `true` when served over https |

## Models (Ollama)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | |
| `CHAT_MODEL` | `llama3.2:1b` | answers and chat titles |
| `EMBED_MODEL` | `nomic-embed-text` | changing it needs a new `QDRANT_COLLECTION` (vectors aren't comparable) |
| `OLLAMA_TIMEOUT` | `300` | seconds; generation on CPU is slow |
| `AGENT_MODEL` | = `CHAT_MODEL` | agent mode; needs tool calling (`qwen2.5:7b` or `llama3.1:8b` work far better) |
| `AGENT_MAX_STEPS` | `4` | tool rounds before the agent must answer |
| `MCP_SERVERS_FILE` | empty | JSON file of extra MCP servers for the agent ([example](../backend/mcp_servers.example.json)) |
| `LLM_PRICE_INPUT_PER_MTOK` | `0.15` | USD per 1M input tokens, for the cost estimate (0 = tokens only) |
| `LLM_PRICE_OUTPUT_PER_MTOK` | `0.60` | USD per 1M output tokens |

## Guardrails

See [guardrails.md](guardrails.md).

| Variable | Default | Meaning |
|---|---|---|
| `GUARDRAILS_MODE` | `enforce` | `enforce` blocks and withholds; `monitor` only counts and audits (to find false positives first); `off` |
| `GUARD_MODEL` | empty | content safety classifier on Ollama, e.g. `llama-guard3:1b`; empty = no content check (injection checks still run) |
| `GUARD_BLOCK_CATEGORIES` | `S1,S2,S3,S4,S9,S10,S11,S12` | Llama Guard 3 categories that are blocked ([table](guardrails.md#content-categories)) |

## Retrieval (Qdrant)

| Variable | Default | Meaning |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6335` | |
| `QDRANT_COLLECTION` | `documents_v2` | |
| `QDRANT_TIMEOUT` | `30` | seconds |
| `MIN_SCORE` | `0.58` | if even the best chunk scores lower, the question isn't about the documents |

## Caching

See [caching.md](caching.md).

| Variable | Default | Meaning |
|---|---|---|
| `EMBED_CACHE` | `true` | reuse the vector of a text embedded before: memory, then PostgreSQL (`embedding_cache`) |
| `EMBED_CACHE_MEMORY_ITEMS` | `10000` | vectors kept in memory per process (≈ 63 MB at 768 dimensions) |
| `SEMANTIC_CACHE` | `true` | answer a chat's first question from a stored answer to a similar one |
| `SEMANTIC_CACHE_THRESHOLD` | `0.95` | minimum cosine similarity for a hit. Different questions can score 0.87 ([measurements](caching.md#choosing-the-threshold)) |
| `SEMANTIC_CACHE_TTL_SECONDS` | `86400` | answers expire after this; every document change also empties the cache |
| `SEMANTIC_CACHE_COLLECTION` | `<QDRANT_COLLECTION>_answers` | Qdrant collection for the answers |

## Rate limits

Per logged-in user (registration: per client IP), in fixed windows, counted in PostgreSQL (`rate_limits`)
so all backend replicas share the count. Over the limit: `429` with `Retry-After`, code `rate_limited`;
the first rejection in a window is audited (`rate_limit.exceeded`), every one is counted in
`rate_limit_rejections_total{limit}`. If PostgreSQL is unreachable, requests go through
(`rate_limit_errors_total`). Format: `<count>/<second|minute|hour|day>`; empty = no limit.

| Variable | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | `false` turns every limit off |
| `RATE_LIMIT_CHAT` | `20/minute` | `/chat` and `/agent/chat` together; `/title` has its own count at the same rate |
| `RATE_LIMIT_UPLOAD` | `60/hour` | `/upload` and `/ingest/batches` requests together (a batch counts once) |
| `RATE_LIMIT_EVAL` | `5/hour` | `/evals/run` |
| `RATE_LIMIT_REGISTER` | `5/hour` | new accounts per IP. Login has its own throttle: 5 failures per username+IP in 5 minutes |

## Storage

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/mychatapp` | **Secret** |
| `MINIO_ENDPOINT` | `localhost:9000` | |
| `MINIO_ACCESS_KEY` | `minioadmin` | **Secret** |
| `MINIO_SECRET_KEY` | `minioadmin123` | **Secret**; the default is refused in production |
| `MINIO_BUCKET` | `documents` | |

## Batch ingestion

| Variable | Default | Meaning |
|---|---|---|
| `INGEST_BATCH_SIZE` | `100` | documents per batch |
| `INGEST_MAX_ATTEMPTS` | `3` | then a document is marked failed |
| `INGEST_LEASE_SECONDS` | `600` | a document "processing" longer than this is assumed abandoned and retried |
| `EMBED_BATCH_SIZE` | `64` | chunks per embedding request |

## Observability

| Variable | Default | Meaning |
|---|---|---|
| `METRICS_PORT` | `9100` | Prometheus scrapes `/metrics` here (0 = off); separate from the API so nginx never exposes it |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty (tracing off) | e.g. `http://otel-collector:4318` |
| `OTEL_SERVICE_NAME` | `mychatapp-backend` / `mychatapp-worker` | name of the traces in Tempo |

## Evaluation

| Variable | Default | Meaning |
|---|---|---|
| `EVAL_MODEL` | = `CHAT_MODEL` | the RAGAS judge; a bigger model gives more trustworthy scores |
| `EVAL_EMBED_MODEL` | = `EMBED_MODEL` | |

## Frontend

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_BASE` | `http://localhost:8000` (dev), `/api` (Docker image) | where the backend is; build-time (`frontend/.env.local`) |
| `BACKEND_HOST` | `backend` | nginx: the backend's hostname (Kubernetes sets the full Service name) |
