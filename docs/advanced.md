# Advanced: measuring, serving, agents, multimodal, security, fine-tuning

The next step after [production-rag.md](production-rag.md): proving which features help, serving models
efficiently, teams of agents, images in documents, attacking your own bot, privacy law, and fine-tuning.
Everything in the app is **off by default**; the settings are in
[configuration.md](configuration.md#advanced). Nothing here has been run end to end yet.

| Area | What | How to use it | Code |
|---|---|---|---|
| Measure | [Compare features](#compare-features) | `python -m evals.compare` | `evals/compare.py` |
| | [Quality gate in CI](#quality-gate) | `.github/workflows/evals.yml` | `evals/gate.py` |
| | [Load test](#load-test) | `k6 run deploy/loadtest/chat.js` | `deploy/loadtest/chat.js` |
| | [Agent evaluation](#agent-evaluation) | `python -m evals.agent_eval` | `evals/agent_eval.py`, `agent_tasks.jsonl` |
| Frontend | approval dialog, upload visibility, SSO button and sign-out | follows the backend's settings | `features/composer.js`, `auth.js`, `documents.js` |
| Serving | [vLLM on a GPU](#vllm) | compose profile `gpu`, `deploy/k8s/gpu` | `deploy/k8s/gpu/vllm.yaml` |
| | [Model routing](#model-routing) | `LLM_ROUTER`, `ROUTER_LARGE_MODEL` | `app/clients/router.py` |
| Agents | [Multi-agent team](#multi-agent-team) | `POST /agent/team` | `app/agents/supervisor.py` |
| | [A2A](#a2a) | `A2A_TOKEN` | `app/api/routes/a2a.py` |
| | [MCP over HTTP with auth](#mcp-over-http) | `python -m app.mcp_server --http` | `app/mcp_server/__main__.py` |
| RAG | [Multimodal](#multimodal) | `VISION_MODEL=llava:7b` | `app/rag/vision.py` |
| | [Structured outputs](#structured-outputs) | `POST /chat/structured` | `app/rag/structured.py` |
| Security | [Red-teaming](#red-teaming) | `python -m evals.redteam` | `evals/redteam.py` |
| | [GDPR: export, erase, retention](#gdpr) | `GET /me/export`, `DELETE /me`, the CronJob | `app/services/privacy.py`, `app/maintenance/retention.py` |
| Fine-tuning | [Embeddings and LoRA](#fine-tuning) | `python -m finetune.*` on a GPU machine | `finetune/` |

## Compare features

Every feature in production-rag.md costs latency, money or a re-index, so turn one on only when the numbers
say it helps your documents. `evals/compare.py` runs the RAGAS eval once per configuration (`baseline`,
`hybrid`, `rerank`, `multi-query`, `hyde`, `prompt-v2`, ...). Each variant runs in a separate process,
because settings are read at import. It prints one table with each metric's change from the baseline.

Small judge models are noisy: use `EVAL_MODEL` = a larger model and the full dataset (`--limit 0`) before
deciding. Retrieval features should move `context_precision` and `context_recall`; if they don't, they
aren't working for you.

## Quality gate

Unit tests can't see that a prompt change made answers worse. `.github/workflows/evals.yml` can:

- **When it runs:** when retrieval, prompt, model or guardrail code changes, nightly, and on demand.
- **What it does:** it indexes `backend/evals/corpus/`, runs the eval, and `evals/gate.py` fails the job
  when a metric falls more than its tolerance below `evals/baseline.json`.
- **Tolerances** must sit just above the run-to-run wobble (measure it). A gate that fails at random gets
  ignored.
- **The baseline** is raised on purpose (`--update-baseline`, committed with the change that earned it),
  never automatically.

To set it up, put the dataset's documents in `backend/evals/corpus/`, then create the first baseline.

## Load test

`deploy/loadtest/chat.js` (k6) ramps virtual users up to `PEAK`, holds, and ramps down. It fails when the
SLOs are missed: under 2% errors, `/documents` p95 under 500 ms, and a whole answer p95 under 30 s.

Watch Grafana while it runs. On CPU, the model saturates long before the API, Qdrant or PostgreSQL. That
tells you to scale model capacity (a GPU, vLLM), not backend pods.

## Agent evaluation

An agent takes a path, not just an answer. Each task in `evals/agent_tasks.jsonl` lists:

- the tools it should call, and the ones it must not call
- strings the answer must contain
- a step budget

The report gives tool recall and precision, forbidden calls, step overruns and goal success. These are
deterministic checks with no judge model, so they're cheap enough to run on every prompt or model change.

## vLLM

Ollama is simple. vLLM is built for throughput:

- **Continuous batching:** requests join the running batch every token.
- **PagedAttention:** the KV cache is stored in pages, so far more requests fit in GPU memory.

The manifests show the settings that matter:

| Setting | What it does |
|---|---|
| `--quantization awq` | 4-bit weights: a 7B model fits in about 5 GB |
| `--enable-prefix-caching` | reuses the computed cache for requests that start the same, like our long system prompt (prompt caching) |
| `--max-model-len` | the context window to reserve memory for |
| `--gpu-memory-utilization` | the share of the GPU vLLM takes |

In Kubernetes, the pod requests `nvidia.com/gpu: 1`, tolerates the GPU node taint, and needs the NVIDIA
device plugin. Scale it by adding GPUs, not CPU.

Hosted APIs have their own prompt caching: Anthropic with `cache_control` markers, OpenAI automatically
for long shared prefixes. Keep the stable part of the prompt first so it can be cached.

## Model routing

Most questions are simple lookups a small model answers well. A few need reasoning. `LLM_ROUTER` sends each
question to the cheapest model that can answer it:

- `heuristic`: rules (length, words like "compare" or "why", several questions at once, a lot of context).
- `llm`: the small model classifies the question first.

The large model is `ROUTER_LARGE_MODEL`, on vLLM when `VLLM_URL` is set. Answers carry an `X-Model-Tier`
header. Measure quality and cost per answer before and after.

## Multi-agent team

`POST /agent/team` uses three roles:

- a **supervisor** that decides with structured output who works next, at most 4 hand-offs
- **specialists**: a researcher with the knowledge-base tools, and an analyst with the SQL tools
- a **writer** that answers from their reports

Each specialist is the same graph as `/agent/chat` with fewer tools, so guardrails, metrics and sources
work unchanged. A team is slower and dearer than one agent: use it when the jobs really differ.

## A2A

The Agent2Agent protocol lets other agents call this one:

- `GET /.well-known/agent-card.json` describes the agent: its skills, its endpoint and how to authenticate.
- `POST /a2a` takes JSON-RPC `message/send` and returns the answer plus sources as a data part.

The bearer token is `A2A_TOKEN`. Behind nginx the card is under `/api/`; A2A clients look at the site root,
so add a proxy rule for `/.well-known/agent-card.json`.

MCP connects an agent to **tools**. A2A connects **agents** to each other.

## MCP over HTTP

`python -m app.mcp_server --http` serves the knowledge base to remote MCP clients over streamable HTTP.
Every request must carry `Bearer <MCP_HTTP_TOKEN>`. The full MCP auth spec is OAuth 2.1 (an authorization
server, PKCE, audience-checked tokens). Put a gateway that does it in front before going beyond a private
network.

## Multimodal

`VISION_MODEL` (`llava:7b`, `qwen2.5vl:7b`) describes images at index time:

- images in PDFs become `[Figure: ...]` lines on their page
- PNG, JPG and TIFF files can be uploaded

The prompt asks for facts (names, numbers, trends), not looks. The alternative, multimodal embeddings with a
vision LLM at answer time, keeps more detail but costs a vision call per question and a second index.

## Structured outputs

`POST /chat/structured` returns validated JSON: `answer`, `found_in_documents`, `confidence` and
`citations[{source, page, quote}]`. It uses three layers:

1. **Schema-constrained decoding** with `with_structured_output`.
2. **Pydantic validation.**
3. **Retry with the error message**, then fail clearly. Half-valid data is never passed on.

## Red-teaming

`evals/redteam.py` sends known attacks through the real API and checks whether they worked, grouped by the
OWASP Top 10 for LLM apps:

| OWASP | Attack | How it's detected |
|---|---|---|
| LLM01 | prompt injection | the injected phrase appears in the answer |
| LLM02 | sensitive data | personal data or secrets appear in the answer |
| LLM05 | HTML/JS output | script or event-handler markup in the answer |
| LLM07 | system-prompt leakage | the canary's shape appears in the answer |
| LLM10 | oversized input | a 200 kB question is accepted instead of rejected |

The script exits 1 on any finding. Add every attack that ever worked to its list. garak and PyRIT
generate thousands of variations.

## GDPR

**`GET /me/export`** downloads everything stored about the user.

**`DELETE /me`** erases the account:

- chats, feedback and memories are deleted, through the database's foreign keys
- private documents are deleted
- the user is removed from documents shared with others
- usage stats are kept with the user id cleared
- the answer cache is emptied
- the audit log records that it happened, without the username

`app/maintenance/retention.py` deletes chats and analytics older than `RETENTION_*`, in batches, run nightly
by `deploy/k8s/jobs/retention-cronjob.yaml`. Logs, traces and backups need their own retention.

## Fine-tuning

| Use | For |
|---|---|
| **RAG** | knowledge: facts that change, must be cited, or are permission-controlled |
| **Fine-tuning** | behaviour: a format, tone or domain language; a small model doing one narrow job well |

Most teams need RAG first and fine-tuning rarely.

- **`finetune/make_dataset.py`**: an LLM writes question/answer pairs from your indexed chunks. Read a
  sample before training on it.
- **`finetune/embeddings.py`**: fine-tunes an embedding model with MultipleNegativesRankingLoss and reports
  held-out recall@5 and MRR before and after. Keep the new model only if they rose, then re-index.
- **`finetune/lora.py`**: QLoRA. The base model is frozen in 4-bit, and small rank-16 adapters are trained
  on examples in the production prompt format. Then merge, convert to GGUF and `ollama create`, and compare
  with `evals/compare.py` before switching.

These scripts need a GPU and their own packages (`uv add --group finetune ...`, listed at the top of each
file).
