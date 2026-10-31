# MyChatApp: local RAG chatbot

A ChatGPT-style chat app that answers questions from your own documents, running entirely on your machine.

- **Frontend:** Vite + vanilla JS (streaming replies, chat history, Markdown, citations, light/dark theme)
- **Backend:** FastAPI + LangChain
- **LLM and embeddings:** Ollama (`llama3.2:1b`, `nomic-embed-text`)
- **Vector store:** Qdrant
- **File storage:** MinIO (S3-compatible)

## How it works

1. Upload a PDF, TXT or MD file. The original is stored in MinIO.
2. The text is extracted (`pypdf`), split into section-aware chunks with a `Document / Section` header, embedded with `nomic-embed-text`, and stored in Qdrant.
3. For each question, the most similar chunks are retrieved. If they're relevant, the model answers from them and the reply shows clickable source chips; otherwise it answers as a normal chat.

## Project structure

```
backend/                    Python API + ingestion workers (one Docker image)
  app/
    main.py                 creates the FastAPI app: middleware, startup, mounts api_router
    core/                   cross-cutting plumbing, used by everything
      config.py             settings from environment variables
      db.py                 SQLAlchemy engine/session, runs migrations at startup
      security.py           password hashing, session tokens, login throttling
      errors.py             AppError types + handlers: one JSON error shape, no internals leaked
      logging.py            JSON logs in production, request id on every line
      middleware.py         request id (X-Request-ID), access log, last-resort 500
      audit.py              audit events: who did what (login, upload, delete, ...)
      metrics.py            every Prometheus metric, served on :9100
      telemetry.py          OpenTelemetry tracing (off unless OTEL_EXPORTER_OTLP_ENDPOINT is set)
    models/                 database tables, one file per area
      user.py, chat.py, document.py, evaluation.py, usage.py
    schemas/                request/response bodies (Pydantic), one file per API area
      chat.py, chats.py, auth.py, documents.py, evals.py
    api/                    HTTP layer (thin: validate input, call rag/services, shape output)
      router.py             all routes in one place; which are public / need a login
      deps.py               shared dependencies (CurrentUser)
      routes/
        health.py           GET /health (liveness), GET /health/ready (dependency checks)
        auth.py             /auth/register, /auth/login, /auth/me, /auth/logout
        chat.py             POST /chat (retrieve -> answer, streamed), POST /title
        agent.py            POST /agent/chat (the model picks tools; streams NDJSON events)
        chats.py            chat history
        documents.py        GET/DELETE /documents, POST /upload, GET /documents/{id}/file
        ingest.py           batch ingestion: POST/GET /ingest/batches, /ingest/retry-failed
        evals.py            RAGAS runs
    prompts/                prompt templates, one file per feature
      rag.py, title.py, agents.py
    rag/                    the RAG technique: knowledge base + answering from it
      chunking.py           text extraction and section-aware chunking
      knowledge_base.py     index / retrieve / delete documents (through VectorStore)
      answer.py             retrieved chunks + chat history -> model messages
    agents/                 the tool-using agent (agent mode)
      loop.py               model -> tool calls -> results -> ... -> answer, as events
      tools.py              its tools, all over MCP: the knowledge-base server + MCP_SERVERS_FILE
    mcp_server/             the knowledge base as an MCP server (search, list, read documents)
      knowledge_base.py     the tools; python -m app.mcp_server runs it over stdio
    ingestion/              batch pipeline for thousands of documents
      queue.py              queue in PostgreSQL: batches, claiming, retries, progress
      worker.py             python -m app.ingestion.worker  (run 3)
      cli.py                python -m app.ingestion.cli FOLDER
    clients/                talking to external servers (the only code that does)
      llm.py                Ollama chat models and embeddings
      storage.py            original files in MinIO
      vector_store.py       chunk vectors: VectorStore interface + Qdrant implementation
    repositories/           database access (the only code that queries PostgreSQL tables)
      doc_store.py          document metadata
      chat_store.py         chat history (scoped to the owner)
      user_store.py         user accounts
      usage_store.py        LLM token usage
    services/               business logic combining the above
      usage.py              token + cost tracking (metrics, database, trace)
  migrations/               Alembic database migrations
  evals/                    RAGAS evaluation dataset and runner
  tests/                    no services needed: fakes.py replaces Qdrant/MinIO/Ollama/PostgreSQL
    conftest.py             shared fixtures (client, logged_in, metric)
    unit/                   functions and modules called directly
    api/                    the HTTP contract, through FastAPI's TestClient
  Dockerfile
frontend/                   Vite + vanilla JS (built into an nginx image)
  src/                      main.js, auth.js, evals.js, lib.js, style.css
  nginx.conf                serves the site, proxies /api to the backend
  Dockerfile
deploy/                     how to run it (see deploy/README.md)
  docker-compose.yml        everything on one machine
  k8s/                      Kubernetes manifests (Deployments, Services, workers, ...)
.github/workflows/          CI/CD: build images, push to ghcr.io, deploy
```

### Where new things go

| Adding... | Put it in |
|---|---|
| a new endpoint | `api/routes/<feature>.py`, then one line in `api/router.py` |
| a new table | `models/<area>.py`, export it in `models/__init__.py`, then an Alembic migration |
| a new setting | `core/config.py` (+ `.env.example`, `deploy/k8s/configmap.yaml`) |
| a new prompt | `prompts/<feature>.py` |
| a new agent tool | a tool on an MCP server: ours in `mcp_server/`, or an external server in `MCP_SERVERS_FILE` |
| a new request/response body | `schemas/<area>.py` |
| a new external system (Redis, S3, an API) | a client in `clients/` |
| a new table query | the table's module in `repositories/` (routes never query tables directly) |
| logic that combines clients, repositories and metrics | `services/` |
| a test | `tests/unit/` (calls code directly) or `tests/api/` (through HTTP); shared fixtures in `conftest.py` |
| a background job | `ingestion/` style: its own package with a worker entry point |

## Prerequisites

Docker containers for the services:

```bash
docker run -d --name ollama -p 11434:11434 ollama/ollama
docker exec ollama ollama pull llama3.2:1b
docker exec ollama ollama pull nomic-embed-text

docker run -d --name qdrant -p 6335:6333 -p 6336:6334 qdrant/qdrant

docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -v minio-data:/data pgsty/minio server /data --console-address ":9001"
```

You also need [uv](https://docs.astral.sh/uv/) and Node.js.

To run everything in containers instead (Docker Compose or Kubernetes), see [deploy/README.md](deploy/README.md).

## Run

Backend (http://localhost:8000):

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

Frontend (http://localhost:5173):

```bash
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend
uv run --no-dev --group test pytest     # ~3 s; Qdrant, MinIO, Ollama and PostgreSQL are faked
uvx ruff check app tests evals migrations
```

CI runs both on every push and pull request; images are only built and deployed when they pass.

## Production behaviour

| Concern | How it's handled |
|---|---|
| Configuration | `APP_ENV=production` refuses to start with dev passwords, a short `JWT_SECRET` or `COOKIE_SECURE=false`, and turns off the dev-only localhost CORS rule |
| Errors | Every error is `{"detail", "code", "request_id"}`. Dependency failures return 503 with a generic message; the real exception is only in the logs |
| Tracing | Each request gets an `X-Request-ID` (or keeps the caller's). It is in the response header, the error body and every log line of that request |
| Logs | One JSON object per line in production (for CloudWatch/Loki/ELK), readable text in development |
| Audit | `app.audit` logger: one event per security-relevant action (`auth.login.failure`, `document.delete`, ...) with user id, request id and client IP; see `app/core/audit.py`. Fields named like secrets are redacted |
| Health | `/health` = liveness (process up, never checks dependencies). `/health/ready` = readiness: 503 only if PostgreSQL is down; Qdrant/MinIO/Ollama outages report `degraded` but keep the pod in rotation |
| Timeouts | Ollama (`OLLAMA_TIMEOUT`, connect 5 s), Qdrant (`QDRANT_TIMEOUT`), MinIO (connect 5 s, read 60 s, 3 retries with backoff), PostgreSQL connect 5 s |
| Migrations | Run at startup under a PostgreSQL advisory lock, so several replicas starting together migrate exactly once |

## Batch ingestion (thousands of documents)

Uploading through the UI indexes one file per request. For bulk loads there is a queue with a pool of workers:

```
ingestion.cli (producer) ──► MinIO (files) + PostgreSQL (one row per document, 100 per batch)
                                               │  SELECT ... FOR UPDATE SKIP LOCKED
                              ┌────────────────┼────────────────┐
                           worker 1         worker 2         worker 3      (python -m app.ingestion.worker)
                              └──► extract ─► chunk ─► embed (64 chunks/request) ─► Qdrant
```

- Batches run in order: batch 2 starts when every document in batch 1 is `ready` or `failed`. Inside a batch, whichever worker is free takes the next document.
- A failed document is retried up to 3 times, then marked `failed` with its error. Files without readable text fail at once.
- If a worker dies, its document is taken over by another worker after 10 minutes. Chunk ids are derived from the document id, so a retry never duplicates chunks.
- On shutdown (Ctrl+C, `docker compose down`, Kubernetes scale-down) a worker finishes its current document first.

```bash
cd backend
uv run python -m app.ingestion.worker              # start one worker per terminal (e.g. 3)
uv run python -m app.ingestion.cli ~/my-documents  # queue every PDF/TXT/MD under the folder
uv run python -m app.ingestion.cli --status        # progress per batch
uv run python -m app.ingestion.cli --failures 4    # why documents in batch 4 failed
uv run python -m app.ingestion.cli --retry-failed
```

The same is available over HTTP: `POST /ingest/batches` (multi-file upload), `GET /ingest/batches`, `GET /ingest/batches/{id}/failures`, `POST /ingest/retry-failed`. In Kubernetes the workers are `deploy/k8s/worker-deployment.yaml` (3 replicas).

The throughput limit is embedding, not the number of workers: all workers share one Ollama. `OLLAMA_NUM_PARALLEL=3` lets it serve them at the same time. Beyond that, more speed needs more Ollama replicas or a GPU.

## Agent mode (MCP)

The **Agent** button in the composer switches replies from `/chat` (always retrieve, then answer) to
`/agent/chat`, where the model decides which tools to use: it can skip the documents for a greeting,
search again with other words, or list and read a whole document to summarize it. Each tool call
shows up above the answer.

All tools come from MCP servers (`app/agents/tools.py`):

- **our knowledge-base server** (`app/mcp_server/`): `search_documents`, `list_documents`, `read_document`.
  It is read-only; uploading and deleting stay behind the logged-in API. The agent connects to it in-process.
- **external servers**, optional: point `MCP_SERVERS_FILE` at a JSON file like
  [`backend/mcp_servers.example.json`](backend/mcp_servers.example.json) (web fetch). Servers that fail to start are skipped.

The model must support tool calling. `llama3.2:1b` works but often picks tools badly or writes a tool
call as text; pull a bigger one and set `AGENT_MODEL`, e.g. `ollama pull qwen2.5:7b`.

The same knowledge-base server works in other MCP clients. For Claude Desktop or Claude Code, run it over stdio:

```json
{
  "mcpServers": {
    "my-chatbot-kb": {
      "command": "uv",
      "args": ["run", "--directory", "D:/path/to/backend", "--env-file", ".env", "python", "-m", "app.mcp_server"]
    }
  }
}
```

It reads Qdrant, PostgreSQL and MinIO directly, so it bypasses the login: use it only on your own machine.

## Evaluation (RAGAS)

[RAGAS](https://docs.ragas.io) scores the pipeline on a set of questions with reference answers in `backend/evals/dataset.jsonl`. Each question is answered with the same retrieval and prompt code as `/chat`, then a local judge model scores:

| Metric | Question it answers |
|---|---|
| `faithfulness` | Is every claim in the answer supported by the retrieved chunks? (hallucination) |
| `answer_relevancy` | Does the answer address the question? |
| `context_precision` | Are the relevant chunks ranked near the top? |
| `context_recall` | Do the retrieved chunks contain everything the reference answer needs? |
| `answer_correctness` | Does the answer match the reference? (opt-in) |

The judge defaults to the chat model (`llama3.2:1b`), so nothing extra needs downloading. Ollama is asked to constrain the judge's output to RAGAS' JSON schema, which keeps a small model's answers parseable. Its scores are still rough (it sometimes marks supported claims as unsupported). RAGAS' own `context_recall` needs a larger judge, so this project uses a small replacement in `backend/evals/metrics.py` that asks one yes/no question per reference sentence. For more trustworthy numbers, pull a larger model and set `EVAL_MODEL` to it.

Open **Evaluation** in the app's sidebar to start a run, follow its progress, and browse past runs. Each question can be expanded to show the answer next to the reference, the retrieved chunks, and any judge errors. On CPU a full run of the 12 questions takes about 10–15 minutes; "Quick" runs only the first 3.

From the command line, with Ollama and Qdrant running and the documents uploaded:

```bash
cd backend
uv run python -m evals.run_ragas                      # all questions, default metrics
uv run python -m evals.run_ragas --limit 3            # quick check
uv run python -m evals.run_ragas --metrics faithfulness answer_correctness
```

It prints per-question scores and a summary. Runs from both the app and the command line are saved as JSON in `backend/evals/results/`. Questions where retrieval returns nothing (best score below `MIN_SCORE`) are skipped for the context metrics and counted in the summary. To add test cases, append lines of `{"question": ..., "reference": ...}` to the dataset. Set `EVAL_MODEL` to use a different judge.

## Configuration

The backend reads these environment variables (defaults shown). To use a file, copy `backend/.env.example` to `backend/.env` and start with `uv run --env-file .env uvicorn app.main:app --reload --port 8000`.

| Variable | Default |
|---|---|
| `OLLAMA_URL` | `http://localhost:11434` |
| `CHAT_MODEL` | `llama3.2:1b` |
| `EMBED_MODEL` | `nomic-embed-text` |
| `QDRANT_URL` | `http://localhost:6335` |
| `QDRANT_COLLECTION` | `documents_v2` |
| `MIN_SCORE` | `0.58` (relevance cut-off for using documents) |
| `APP_ENV` | `development` (`production` enables the startup checks and JSON logs) |
| `LOG_LEVEL` | `INFO` |
| `OLLAMA_TIMEOUT` / `QDRANT_TIMEOUT` | `300` / `30` seconds |
| `MINIO_ENDPOINT` | `localhost:9000` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `minioadmin` / `minioadmin123` |
| `MINIO_BUCKET` | `documents` |
| `CORS_ORIGINS` | `*` (comma-separated list) |
| `AGENT_MODEL` | same as `CHAT_MODEL` (needs tool calling, see Agent mode) |
| `AGENT_MAX_STEPS` | `4` (tool rounds before the agent must answer) |
| `MCP_SERVERS_FILE` | empty (JSON file of extra MCP servers for the agent) |
| `EVAL_MODEL` | same as `CHAT_MODEL` (RAGAS judge) |
| `EVAL_EMBED_MODEL` | same as `EMBED_MODEL` |

The frontend calls the backend at `VITE_API_BASE` (default `http://localhost:8000`); set it in `frontend/.env.local`.

The MinIO credentials are local-development defaults. Change them before exposing the app anywhere.
