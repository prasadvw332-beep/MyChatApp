# Observability: seeing what the app is doing

Three questions, three tools, one screen:

| Question | Tool | Example |
|---|---|---|
| **How much, how fast, how often?** (numbers over time) | **Prometheus** (metrics) | "p95 time to the first word went from 3 s to 12 s at 2 pm" |
| **What happened inside this one request?** | **Tempo** (traces, sent by OpenTelemetry) | "this chat spent 16 s in Ollama and 0.004 s in Qdrant" |
| **What exactly went wrong?** | **logs** (stdout, JSON in production) | "ConnectionError: Qdrant refused the connection" |
| *all of the above in one place* | **Grafana** | the *MyChatApp · Production overview* dashboard |

The usual path: the **dashboard** shows *that* something is wrong, a **trace** shows *where*, the **logs** show *why*.
They are linked: every log line carries the trace id (`t=1a2b3c4d` in development, `"trace_id"` in JSON).

```
 backend / workers ─┬─ :9100/metrics ◄── scraped every 15 s ── Prometheus ◄─┐
                    │                                                        ├── Grafana
                    └─ spans (OTLP) ──► OTel Collector ──► Tempo ◄───────────┘
```

How it is deployed (compose and Kubernetes): [deploy/README.md](../deploy/README.md#observability-prometheus-opentelemetry-grafana).

## Where to look

With `make up` (docker compose):

| What | URL |
|---|---|
| Grafana dashboard | http://localhost:3000/d/mychatapp (anonymous view; `admin` / `GRAFANA_ADMIN_PASSWORD` to edit) |
| Traces | Grafana → Explore → Tempo → Search, or click a Trace ID in the dashboard's "Recent requests" table |
| Prometheus | http://localhost:9090 (Status → Targets: backend + every worker should be UP) |

## The dashboard, top to bottom

The order follows the questions on-call asks: *is anything broken? → is it within the SLO? → where? → why?*

1. **Service status**: critical alarms and warnings firing, availability, error budget left, time to first word,
   and one green/red lane per component (API, workers, PostgreSQL, Qdrant, MinIO, Ollama, the telemetry stack),
   next to the list of active alerts.
2. **Golden signals**: requests, errors, latency, answers, active users, cost, ingest backlog. Green/orange/red.
3. **SLOs & error budget**: availability (99.9%), API latency (99% under 1 s), first word (95% under 10 s), and the
   burn rate with its alert levels (6x warning, 14.4x critical). Targets and alerts live in
   [alerts.yml](../deploy/observability/alerts.yml).
4. **Traffic & errors**: requests and errors per route (red = server errors, orange = bad requests, blue = not logged in).
5. **AI pipeline**: *time per step* (ranked: generation, embedding, vector search, SQL), first word vs full answer,
   questions answered without documents, agent tool calls.
6. **Caching**, **Tokens & cost** (with *usage by user* from PostgreSQL) and **Guardrails** (blocked and flagged
   events, poisoned document chunks, guard model errors).
7. **Document ingestion**: documents indexed / retried / failed, time per document, and the queue itself from
   PostgreSQL: queued, processing, stuck, oldest wait, batches, the last error of each failed file.
8. **Data stores & model server**: PostgreSQL (connections, cache hit ratio, transactions, long transactions),
   Qdrant (vectors, search latency, errors), MinIO (files, storage, free disk), Ollama (calls and latency, from traces).
9. **Traces**: service map (who calls whom) and recent requests.
10. **Telemetry pipeline** and **Pods & resources** (collapsed): spans dropped on the way to Tempo, Prometheus series,
    scrape targets; memory, CPU, up/down per pod.

The top of the dashboard is a **live system map**: one box per component, tinted green (up) or red (down),
with a live number inside and animated arrows in the direction requests flow. It refreshes every 10 s.

**No traffic, no graphs.** Almost every panel is a rate of what users do. On a laptop with nobody using the app,
start the synthetic users ([deploy/loadgen/loadgen.py](../deploy/loadgen/loadgen.py)): they log in, browse, ask
questions (some repeated, some follow-ups, some in agent mode), occasionally fail on purpose (wrong password,
missing file, a prompt injection) and queue a small generated story for the workers every 10 minutes.

```bash
docker compose -f deploy/docker-compose.yml --profile demo up -d --no-deps loadgen   # start
docker compose -f deploy/docker-compose.yml --profile demo stop loadgen              # stop
```

Every graph also shows **red marks** where an alert fired and **blue marks** where a backend or worker pod
(re)started, so a spike can be lined up with a deploy or a crash.

Hover the (i) on any panel for what it means. The dashboard is generated: edit
[build_dashboard.py](../deploy/observability/grafana/build_dashboard.py), not the JSON.

## Reading a trace

Click a Trace ID: one bar per step, the longest bar is where the time went.

```
POST /chat ───────────────────────────────────────── 22.7 s   the whole request
 ├─ SELECT                                           0.0005 s  login check (PostgreSQL)
 ├─ rag.retrieve                                     0.08 s    search the documents:
 │   ├─ ollama POST /api/embed                       0.02 s      question -> vector
 │   └─ qdrant POST .../points/query                 0.004 s     closest chunks
 └─ ollama POST /api/chat ────────────────────────── 5.8 s to the first word, then streaming
```

## Metrics

All defined in [backend/app/core/metrics.py](../backend/app/core/metrics.py):

| Metric | Labels | What |
|---|---|---|
| `http_requests_total` | method, route, status | requests (route is the template, e.g. `/chats/{chat_id}`) |
| `http_request_duration_seconds` | method, route | including a streamed body |
| `llm_time_to_first_token_seconds` | endpoint (chat, agent) | how long the user waits for the first word |
| `llm_stream_duration_seconds` | endpoint | the whole answer |
| `llm_tokens_total` | endpoint, model, kind (input, output) | tokens, as reported by the model server |
| `llm_cost_usd_total` | endpoint, model | estimate at `LLM_PRICE_*_PER_MTOK` |
| `llm_output_tokens_per_second` | model | generation speed |
| `retrieval_duration_seconds` | | embedding + vector search |
| `retrieval_no_context_total` | | questions where no chunk passed `MIN_SCORE` |
| `agent_tool_calls_total` | tool, ok | |
| `guardrail_events_total` | check, action (blocked, flagged, error) | what the [guardrails](guardrails.md) found |
| `rate_limit_rejections_total` | limit (chat, title, upload, eval, register) | requests refused with 429 ([rate limits](configuration.md#rate-limits)) |
| `rate_limit_errors_total` | | limit checks that failed (PostgreSQL down); those requests went through |
| `ingest_documents_total` | outcome (indexed, retry, failed) | |
| `ingest_duration_seconds`, `ingest_chunks_total` | | per document |

Per-user token usage is not a metric (one time series per user doesn't scale in Prometheus): it is the
`llm_usage` table, which Grafana reads directly. Tempo also turns spans into `traces_spanmetrics_*` metrics,
which feed the *time per step* panel and the service map.
