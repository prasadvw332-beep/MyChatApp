# Production RAG features

What separates a RAG demo from a system a company runs: better retrieval, permissions, agents that can
pause and remember, LLM operations, security and a platform around it. Each feature here is **off by
default** and switched on with a setting, so you can turn them on one at a time and compare. The settings
are listed in [configuration.md](configuration.md#production-rag-features).

None of this has been run end to end yet. The code is written to show how each piece works and where it
plugs in. Several features need a package that isn't installed by default; the table says which.

| Area | Feature | Turn on with | Code | Extra package |
|---|---|---|---|---|
| Retrieval | [Hybrid search](#hybrid-search) | `HYBRID_SEARCH=true` | `rag/sparse.py`, `rag/fusion.py`, `clients/vector_store.py` | |
| | [Reranking](#reranking) | `RERANK=cross_encoder` or `api` | `rag/rerank.py` | `sentence-transformers` (local) |
| | [Query rewriting](#query-rewriting) | `QUERY_REWRITE=condense`, `multi`, `hyde` | `rag/query.py` | |
| | [GraphRAG](#graphrag) | `GRAPH_RAG=true` | `rag/graph.py` | |
| Permissions | [Document ACLs](#document-permissions) | `ACL_ENABLED=true` | `core/acl.py`, `routes/documents.py` | |
| | [Deletion sync](#deletion-sync) | `cli <source> --sync` | `ingestion/cli.py` | |
| Agent | [Checkpoints and approval](#human-approval) | `AGENT_APPROVAL_TOOLS=...` | `agents/loop.py`, `agents/checkpoint.py` | `langgraph-checkpoint-postgres` (for postgres) |
| | [Long-term memory](#long-term-memory) | `AGENT_MEMORY=true` | `agents/memory.py` | |
| | [Text-to-SQL](#text-to-sql) | `SQL_AGENT_URL=...` | `agents/sql_tool.py` | |
| LLM ops | [Gateway and fallback](#llm-gateway-and-fallback) | `LLM_FALLBACK=provider:model` | `clients/llm.py` | `langchain-openai` or `langchain-anthropic` |
| | [LLM tracing](#llm-tracing) | `LANGFUSE_*` | `core/llm_tracing.py` | `langfuse` |
| | [Prompt versions and A/B tests](#prompt-ab-tests) | `PROMPT_AB=v1=50,v2=50` | `prompts/registry.py` | |
| Security | [PII redaction](#pii-redaction) | `PII_REDACTION=index`, `llm`, `both` | `guardrails/pii.py` | `presidio-*` (optional) |
| | [Single sign-on](#single-sign-on) | `OIDC_*` | `core/oidc.py`, `routes/auth.py` | |
| Platform | [Redis](#redis) | `REDIS_URL=...` | `clients/redis_counts.py` | `redis` |
| | [Event-driven ingestion](#event-driven-ingestion) | `INGEST_WEBHOOK_TOKEN=...` | `routes/events.py` | |

The database changes for all of them are in one migration, `0008_production_rag.py`, which runs at startup
like the others. It's safe with everything off: new columns have defaults, new tables stay empty.

## The chat request with everything on

```
question + history
  └─ query rewriting ──────────── condense the follow-up, add variants or a HyDE passage
       └─ for each query: hybrid search (dense + BM25, RRF), filtered by the user's ACL principals
            └─ RRF across queries ─ relevance gate ─ rerank 20 → 5 ─ injection screening
                 + knowledge-graph facts for the entities in the question
                      └─ prompt version (A/B) ─ PII redaction ─ model (with fallback) ─ traced in Langfuse
```

## Hybrid search

Embeddings match meaning ("time off" finds "leave policy") but miss exact tokens: product codes, error
numbers, rare names. BM25 keyword search is the opposite. Hybrid search runs both and merges the two
rankings with **Reciprocal Rank Fusion** (`rag/fusion.py`): each chunk scores `1 / (60 + rank)` per list.
Ranks are comparable across searches; raw scores aren't.

- **BM25 as sparse vectors** (`rag/sparse.py`): each chunk becomes `{token index: weight}`. The
  term-frequency part is computed at indexing time. Qdrant multiplies in the IDF part at query time
  (`Modifier.IDF`), so it stays right as documents come and go.
- Token indexes come from `crc32`, not Python's `hash()`, which differs per process.
- The hybrid store uses its **own collection** (`<QDRANT_COLLECTION>_hybrid`), so re-index the documents
  after turning it on.
- The relevance gate still uses the dense cosine score, because an RRF score can't say whether the
  question is about the documents at all.

## Reranking

Search embeds the question and each chunk separately: fast, but the model never sees them side by side.
A **cross-encoder** reads each (question, chunk) pair together and scores it far more accurately. It's too
slow to run over everything, so the pipeline fetches `RERANK_CANDIDATES` (20) chunks and the reranker keeps
the best `TOP_K` (5).

- `RERANK=cross_encoder` runs a local model (`BAAI/bge-reranker-base`).
- `RERANK=api` calls a hosted `/rerank` endpoint (Cohere, Jina and Voyage share the format).
- If the reranker fails, the retrieval order is kept.

## Query rewriting

- **`condense`**: "what about Pune?" can't be searched on its own. The model rewrites it into a standalone
  question using the chat history.
- **`multi`**: the question plus `QUERY_VARIANTS` rephrasings, each searched, results fused with RRF.
  One unlucky wording stops deciding the answer.
- **`hyde`** (Hypothetical Document Embeddings): the model writes a fake answer and that is searched, because
  an answer looks more like the chunk you want than a question does. It's used only for searching, never
  shown.

Each costs a model call before the search starts, so the first token arrives later. The answer is still
written for the user's own question.

## GraphRAG

When an answer is spread over documents ("who manages the people on Project Atlas?"), no single chunk
looks like the question.

- **At indexing time**, the LLM extracts `(entity, relation, entity)` triples from each chunk into
  `graph_entities` and `graph_relations`.
- **At question time**, the entities named in the question are expanded two hops through the graph, and
  the facts go into the prompt next to the chunks.

It costs one model call per chunk, in the workers rather than the chat request. Small models extract noisy
triples. Larger setups use Microsoft GraphRAG (community summaries) or a graph database such as Neo4j.

## Document permissions

With `ACL_ENABLED=true`, every document has an access list of principals: `public`, `user:<id>` and
`group:<name>`. Groups come from SSO.

- **Search:** filtering happens inside the vector search (a Qdrant filter on each chunk's `acl`), never
  afterwards. The model repeats whatever reaches its context, so the context is the only safe place to
  enforce it.
- **Document routes:** the document list, file download, tags, delete and `PUT /documents/{id}/acl` check
  the same list. A forbidden document answers 404, so it doesn't reveal that the id exists.
- **Uploads** take `visibility` = `public`, `private` or `groups`. Users can share only with groups they
  belong to.
- **Semantic cache:** off while ACLs are on. One shared answer cache would leak answers across users.
- **Agent tools:** the agent's knowledge-base tools see only public documents, because that MCP server is
  shared and doesn't know who's asking (safe by default).
- **Re-index first:** chunks indexed before ACLs existed have no `acl` field and won't match the filter.
  Existing rows get `["public"]` from the migration.

## Deletion sync

`cli <source> --sync` deletes documents that were imported from that source earlier but whose file is gone.
Deleting is the dangerous half: a wrong path or an expired token looks exactly like "everything was
deleted". So an empty listing never deletes, and neither does a run that would remove more than half of
the source's documents, unless you pass `--force`. The Airflow DAG runs every import with `--sync`.

## Human approval

LangGraph's **checkpointer** saves the graph state after every node, under the run's thread id. With
`AGENT_APPROVAL_TOOLS=query_database,send_email`, a call to one of those tools stops in the `approve` node:

1. `interrupt()` saves the state, and the stream ends with `{"type": "approval_required", "thread_id", "calls"}`.
2. The user decides. `POST /agent/resume {"thread_id", "approved"}` continues from the checkpoint.
3. If the user declines, the model gets "the user declined" as that tool's result and answers without it.

- **Checkpointer:** `AGENT_CHECKPOINTER=memory` works with one replica. `postgres` lets any replica resume
  and survives restarts.
- **Ownership:** thread ids start with the user id, so nobody can resume someone else's run.
- **No side effects before `interrupt()`:** on resume, the node runs again from the top, so anything before
  the interrupt would happen twice.

## Long-term memory

The conversation is short-term memory: it ends with the chat. With `AGENT_MEMORY=true`, the agent has a
`remember` tool that stores facts the user tells it about themselves in `user_memories`. The latest 20 go
into the system prompt of every run. The tool is created per request with the user id bound in, so a prompt
can't write into someone else's memory.

Production versions add extraction after each chat, deduplication and updates, semantic recall, and a page
where users can see and delete what's stored.

## Text-to-SQL

Similarity search can't count or sum. With `SQL_AGENT_URL`, the agent gets two tools:

- `describe_database` lists the tables and columns.
- `query_database` runs one SELECT and returns up to 50 rows.

Model-written SQL is untrusted input. The protection that holds is a **read-only database user**, ideally on
views made for the purpose. The code adds more layers on top: a single statement, SELECT or WITH only, a
READ ONLY transaction, a 5-second timeout, and always a rollback. Consider putting `query_database` in
`AGENT_APPROVAL_TOOLS`.

## LLM gateway and fallback

`LLM_FALLBACK=openai:gpt-4o-mini` (or `anthropic:...`) puts a second model behind the chat and agent models
using LangChain's `with_fallbacks()`. If Ollama fails, the call is retried on the fallback.

The production pattern is a **LiteLLM proxy**: one OpenAI-compatible endpoint in front of every provider,
with routing, retries, budgets and keys in one place. Use `LLM_FALLBACK=openai:<model>` with
`LLM_FALLBACK_BASE_URL=http://litellm:4000`.

A cloud fallback sends questions and documents out of your network, so consider `PII_REDACTION=llm`.

## LLM tracing

OpenTelemetry shows how long each step took. **Langfuse** shows what happened: the full prompt with the
retrieved chunks, the output, tokens and cost, and each agent step, grouped by user. That's what you need
for "why was this answer wrong?". It's enabled by `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.

Traces contain document text and questions: protect the tracing backend like a database.

## Prompt A/B tests

Prompts are versioned like code (`prompts/registry.py`: `v1` and `v2` of the RAG prompt).

- `PROMPT_AB=v1=50,v2=50` assigns each **user** to a version, by a hash of their user id, so a chat never
  switches style halfway.
- Each answer records its version in `prompt_runs`. Feedback carries the same request id, so
  `GET /prompts/experiments` shows the thumbs-up rate per version.
- Decide on hundreds of ratings per side, not ten. Run the RAGAS evals per version before exposing users.

## PII redaction

- `PII_REDACTION=index` replaces personal data with placeholders before chunks are embedded and stored.
- `llm` redacts the context and question before they reach the model.
- `both` does both.

The default regex engine handles structured data: emails, phones, card numbers (with a Luhn check), IBAN,
IP addresses, SSN, PAN and Aadhaar. `PII_ENGINE=presidio` adds names, places and organisations using a
language model.

Redaction costs answers: "what is Asha's phone number?" can't be answered from `[PHONE]`. Decide per kind of
data which matters more.

## Single sign-on

OpenID Connect with the authorization-code flow and PKCE. `GET /auth/oidc/login` redirects to the provider,
and the provider redirects back to `/auth/oidc/callback`.

- **Checks:** state (CSRF), nonce (replay) and the PKCE verifier are kept in a 10-minute signed cookie.
- **ID token:** verified against the provider's published keys (JWKS), plus issuer, audience and expiry.
- **Account:** the user is found or created by the token's `sub`. The groups claim becomes `user.groups`,
  which feeds the document ACLs. The account gets a random password nobody knows, so password login stays
  closed for it.

Set `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `OIDC_REDIRECT_URI`. Provider notes are at the
top of `core/oidc.py`.

### Microsoft Entra ID

Set `ENTRA_TENANT_ID` instead of `OIDC_ISSUER`; the issuer follows from it. `core/entra.py` handles what
Entra does differently:

| Entra behaviour | What the code does |
|---|---|
| Groups arrive as object ids (GUIDs) | `ENTRA_GROUP_NAMES="<id>=hr,<id>=finance"` maps them to names; unmapped ids are kept and still work in ACLs |
| Over 200 groups, the token says "ask Graph" instead (groups overage) | calls Microsoft Graph `/me/transitiveMemberOf` with the login's access token (nested groups included, by display name) |
| App roles in the `roles` claim | added to the user's groups as `role:<name>` |
| `sub` differs per app registration | accounts are keyed by tenant + `oid`, the user's one id in the tenant |
| `ENTRA_TENANT_ID=organizations` accepts any company's accounts | the issuer is checked per token, and its tenant must be in `ENTRA_ALLOWED_TENANTS` |
| The Entra session outlives our cookie | `POST /auth/logout` returns `sso_logout`, Entra's sign-out URL, for the browser to visit next |

If Graph fails during login, the user still gets in, but with no groups: least privilege, not a lockout. The
app registration steps (redirect URI, client secret, groups claim, Graph permissions with admin consent) are
listed at the top of `core/entra.py`.

## Redis

With `REDIS_URL`, the rate-limit counters move from a PostgreSQL upsert per request to Redis
`INCR` + `EXPIRE` in one transaction: microseconds instead of a database write, and windows expire by
themselves. Losing Redis only resets the counters. Never keep anything only in Redis.

## Event-driven ingestion

`POST /ingest/events/s3` receives S3/MinIO bucket notifications:

- **created:** the file is downloaded and queued, and the workers index it within seconds.
- **removed:** the document is deleted.

It's authenticated by `INGEST_WEBHOOK_TOKEN` as a Bearer token, compared in constant time; unset means the
endpoint is off. Events arrive "at least once", and that's harmless here because a duplicate has the same
content hash.

Keep the nightly `--sync` import too: events give freshness, and the sync repairs whatever events were lost.
The MinIO setup commands are at the top of `routes/events.py`.

### Through Kafka

At scale the bucket publishes to a Kafka topic instead, and `python -m app.ingestion.kafka_consumer` reads it
at the pace the workers can index (compose profile `kafka`: one KRaft broker and the consumer). Both paths
share the same handler (`app/ingestion/events.py`).

| Problem | How the consumer handles it |
|---|---|
| A burst of 50,000 uploads | They wait in the topic (7 days of retention) instead of flooding an endpoint |
| A crash mid-event | Offsets are committed only after an event is handled (at-least-once); handling is idempotent |
| MinIO briefly down | Retries with backoff, up to `KAFKA_MAX_ATTEMPTS`; the partition waits, so order is kept |
| A poison message | Goes to the dead-letter topic `bucket-events.dlq` with the error, and the consumer moves on |
| More throughput | More consumers in the same group, up to the number of partitions |
| A deploy or scale-down | SIGTERM finishes the event, commits, and leaves the group cleanly |

It needs `uv add confluent-kafka`. Production runs 3+ brokers with replication, or a managed Kafka (Amazon MSK,
Confluent Cloud, or Strimzi in Kubernetes).
