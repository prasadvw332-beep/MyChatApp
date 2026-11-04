"""Settings, read from environment variables (see .env.example for the full list)."""

import os

# development | production. Production refuses to start with insecure defaults (see validate()).
APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# json: one object per line, for Loki (docker compose and Kubernetes set it); text: readable in a terminal
LOG_FORMAT = os.getenv("LOG_FORMAT", "json" if IS_PRODUCTION else "text").lower()

# Observability (app/core/metrics.py, app/core/telemetry.py)
# Prometheus scrapes this port; it is separate from the API so nginx never exposes it. 0 = off.
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))
# OTLP/HTTP endpoint of the OpenTelemetry Collector, e.g. http://otel-collector:4318. Empty = no tracing.
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "")  # default: chosen by the process (backend / worker)

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:1b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
# a hung model server must not hold a request (and a worker) forever; generation on CPU is slow
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))

# Agent (app/agents/): the model must support tool calling. llama3.2:1b does, but often picks tools
# badly; qwen2.5:7b or llama3.1:8b are much more reliable.
AGENT_MODEL = os.getenv("AGENT_MODEL", CHAT_MODEL)
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "4"))  # tool rounds before it must answer
# optional JSON file of extra MCP servers for the agent (see mcp_servers.example.json)
MCP_SERVERS_FILE = os.getenv("MCP_SERVERS_FILE", "")

# Token cost (app/services/usage.py): USD per 1 million tokens. Ollama itself costs nothing per token,
# so the defaults are a small hosted model's list price: "what this traffic would cost on a paid API".
# Set your provider's real prices when you switch; 0 and 0 = track tokens only.
LLM_PRICE_INPUT_PER_MTOK = float(os.getenv("LLM_PRICE_INPUT_PER_MTOK", "0.15"))
LLM_PRICE_OUTPUT_PER_MTOK = float(os.getenv("LLM_PRICE_OUTPUT_PER_MTOK", "0.60"))

# Guardrails (app/guardrails/, docs/guardrails.md)
# enforce = block and withhold; monitor = only log, count and audit (to tune before enforcing); off
GUARDRAILS_MODE = os.getenv("GUARDRAILS_MODE", "enforce").lower()
if GUARDRAILS_MODE not in ("enforce", "monitor", "off"):
    raise ValueError(f"GUARDRAILS_MODE must be enforce, monitor or off, not {GUARDRAILS_MODE!r}")
# Content safety classifier on Ollama, e.g. llama-guard3:1b. Empty = no content check (injection checks still run).
GUARD_MODEL = os.getenv("GUARD_MODEL", "")
# Llama Guard 3 hazard categories that are blocked. Left out by default because they hit normal questions
# about uploaded files: S5 defamation, S6 specialized (legal/medical/financial) advice, S7 privacy (a CV's
# phone number), S8 intellectual property, S13 elections, S14 code interpreter abuse.
GUARD_BLOCK_CATEGORIES = set(os.getenv("GUARD_BLOCK_CATEGORIES", "S1,S2,S3,S4,S9,S10,S11,S12").upper().split(","))

# Qdrant
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6335")
# v2: new chunk format + nomic task prefixes, so vectors aren't comparable with the old collection
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "documents_v2")
QDRANT_TIMEOUT = int(os.getenv("QDRANT_TIMEOUT", "30"))
# If even the best chunk scores below this, the question isn't about the documents.
# Measured with nomic-embed-text + prefixes: related questions >= ~0.60, unrelated <= ~0.57.
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.58"))

# Caching (app/cache/, docs/caching.md)
# Embeddings: the same text always gets the same vector, so it is computed once. An LRU in each process,
# then PostgreSQL (shared by backend replicas and workers, kept across restarts and re-indexing).
EMBED_CACHE = os.getenv("EMBED_CACHE", "true").lower() == "true"
EMBED_CACHE_MEMORY_ITEMS = int(os.getenv("EMBED_CACHE_MEMORY_ITEMS", "10000"))
# Answers: a first question this similar (cosine) to one already answered gets the stored answer, without
# retrieval or the chat model. Measured with nomic-embed-text + prefixes: rewordings of a question score
# 0.975-0.997, questions that differ in one key word ("phone" vs "email", "phone model") 0.83-0.87. A wrong
# cached answer is worse than a slow one, so stay well above the second group. Every document change empties it.
SEMANTIC_CACHE = os.getenv("SEMANTIC_CACHE", "true").lower() == "true"
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.95"))
SEMANTIC_CACHE_TTL_SECONDS = int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", "86400"))
# named after the documents collection: a new embedding model (new QDRANT_COLLECTION) gets a fresh cache too
SEMANTIC_CACHE_COLLECTION = os.getenv("SEMANTIC_CACHE_COLLECTION") or f"{QDRANT_COLLECTION}_answers"

# PostgreSQL (app data: eval results)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/mychatapp")

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")  # dev default, rejected in production
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "documents")

# API
# Cookies need explicit origins ("*" is not allowed together with credentials)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

# Login sessions: a signed JWT in an httpOnly cookie. Set JWT_SECRET so sessions survive restarts.
JWT_SECRET = os.getenv("JWT_SECRET", "")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"  # true when served over https
MAX_UPLOAD_MB = 20

# Rate limits (app/core/rate_limit.py): "<count>/<second|minute|hour|day>" per user (registration: per IP),
# counted in PostgreSQL so all backend replicas share them. Empty = no limit for that group.
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "20/minute")  # /chat and /agent/chat together; /title separately
RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "60/hour")  # /upload and /ingest/batches requests
RATE_LIMIT_EVAL = os.getenv("RATE_LIMIT_EVAL", "5/hour")  # RAGAS runs: minutes of model time each
RATE_LIMIT_REGISTER = os.getenv("RATE_LIMIT_REGISTER", "5/hour")  # new accounts per IP

# Batch ingestion (app/ingestion/)
INGEST_BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "100"))  # documents per batch
INGEST_MAX_ATTEMPTS = int(os.getenv("INGEST_MAX_ATTEMPTS", "3"))  # then the document is marked failed
# a document 'processing' for longer than this is assumed to belong to a dead worker and is retried
INGEST_LEASE_SECONDS = int(os.getenv("INGEST_LEASE_SECONDS", "600"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "64"))  # chunks per embedding request
# Text extraction (app/rag/extractors/): basic (pypdf + python-docx), docling, unstructured or azure.
# The richer ones also read PPTX, XLSX, HTML and images, and OCR scanned PDFs.
EXTRACTOR = os.getenv("EXTRACTOR", "basic").lower()
AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT", "")  # https://<name>.cognitiveservices.azure.com
AZURE_DI_KEY = os.getenv("AZURE_DI_KEY", "")
# Each PDF page's extracted text is kept in PostgreSQL (extraction_cache), so an edited document only re-extracts
# the pages that changed (app/cache/extraction.py). Only used where extraction is slow or paid: an EXTRACTOR other
# than basic, or basic with VISION_MODEL.
EXTRACT_CACHE = os.getenv("EXTRACT_CACHE", "true").lower() == "true"
# docling/unstructured/azure: a PDF page whose text layer has at least this many characters is read with pypdf
# instead, so only scanned pages are OCR'd. Those pages lose the extractor's tables and headings. 0 = off.
EXTRACT_TEXT_LAYER_MIN_CHARS = int(os.getenv("EXTRACT_TEXT_LAYER_MIN_CHARS", "0"))
# S3 source for bulk import (cli.py s3://bucket/prefix); unset = the local MinIO, handy for trying it out.
# Use a read-only key: the importer only lists and downloads.
S3_SOURCE_ENDPOINT = os.getenv("S3_SOURCE_ENDPOINT", MINIO_ENDPOINT)  # AWS: s3.amazonaws.com
S3_SOURCE_ACCESS_KEY = os.getenv("S3_SOURCE_ACCESS_KEY", MINIO_ACCESS_KEY)
S3_SOURCE_SECRET_KEY = os.getenv("S3_SOURCE_SECRET_KEY", MINIO_SECRET_KEY)
S3_SOURCE_SECURE = os.getenv("S3_SOURCE_SECURE", "false").lower() == "true"  # true for AWS (https)
# Google Drive source (gdrive://<folder id>): path to a service account's JSON key; share the folder with its email
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
# SharePoint source (sharepoint://...): an Entra app registration with the Graph permission Sites.Read.All
SHAREPOINT_TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID", "")
SHAREPOINT_CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID", "")
SHAREPOINT_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
# Confluence Cloud source (confluence://<site>/<space key>): an account's email and API token
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")
# Azure Blob source (azblob://<account>/<container>): a SAS token with Read + List permission only
AZURE_STORAGE_SAS_TOKEN = os.getenv("AZURE_STORAGE_SAS_TOKEN", "")
# Notion source (notion://<page id>): an internal integration's secret; share the page with the integration
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
# Jira Cloud source (jira://<site>/<project key>): an account's email and API token
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
# Slack source (slack://<channel id>): a bot token (xoxb-...), and how many days of history to import
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_HISTORY_DAYS = int(os.getenv("SLACK_HISTORY_DAYS", "90"))
# Database source (sql://<table>): a SQLAlchemy URL, ideally of a read-only user
SQL_SOURCE_URL = os.getenv("SQL_SOURCE_URL", "")
# Website source (https://...): stop crawling after this many pages
WEB_MAX_PAGES = int(os.getenv("WEB_MAX_PAGES", "200"))


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


# ---------- Production RAG features (docs/production-rag.md). All off by default. ----------

# Retrieval (app/rag/): hybrid = dense vectors + BM25 keywords fused with RRF, in its own collection
HYBRID_SEARCH = _flag("HYBRID_SEARCH")
# Reranking after retrieval: off | cross_encoder (local, sentence-transformers) | api (Cohere/Jina-style /rerank)
RERANK = os.getenv("RERANK", "off").lower()
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")  # cross_encoder: a Hugging Face model
RERANK_API_URL = os.getenv("RERANK_API_URL", "https://api.cohere.com/v2/rerank")
RERANK_API_KEY = os.getenv("RERANK_API_KEY", "")
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "20"))  # fetched before reranking down to TOP_K
# Query rewriting before search: off | condense (follow-up -> standalone) | multi (N variants) | hyde
QUERY_REWRITE = os.getenv("QUERY_REWRITE", "off").lower()
QUERY_VARIANTS = int(os.getenv("QUERY_VARIANTS", "3"))  # multi
# Knowledge graph next to the vectors: entities and relations extracted by the LLM at index time
GRAPH_RAG = _flag("GRAPH_RAG")
# keep each chunk's extracted triples (graph_cache): re-indexing an edited document only asks the model about
# chunks whose text changed
GRAPH_CACHE = os.getenv("GRAPH_CACHE", "true").lower() == "true"

# Document permissions: each document lists who may see it ("public", "user:<id>", "group:<name>")
ACL_ENABLED = _flag("ACL_ENABLED")

# Agent
AGENT_CHECKPOINTER = os.getenv("AGENT_CHECKPOINTER", "memory").lower()  # memory | postgres
# tools that need the user's OK before they run (human-in-the-loop), comma-separated names
AGENT_APPROVAL_TOOLS = {t.strip() for t in os.getenv("AGENT_APPROVAL_TOOLS", "").split(",") if t.strip()}
AGENT_MEMORY = _flag("AGENT_MEMORY")  # long-term memory per user: remember/recall tools
SQL_AGENT_URL = os.getenv("SQL_AGENT_URL", "")  # text-to-SQL tool: SQLAlchemy URL of a READ-ONLY user
SQL_AGENT_MAX_ROWS = int(os.getenv("SQL_AGENT_MAX_ROWS", "50"))

# LLM gateway: an extra model tried when the primary fails, "provider:model" (openai:gpt-4o-mini,
# anthropic:claude-haiku-5-5, or openai:<name> behind a LiteLLM proxy at LLM_FALLBACK_BASE_URL)
LLM_FALLBACK = os.getenv("LLM_FALLBACK", "")
LLM_FALLBACK_BASE_URL = os.getenv("LLM_FALLBACK_BASE_URL", "")
# LLM tracing in Langfuse (prompt, retrieved context, tokens, cost per answer)
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
# Prompt A/B test: "v1=50,v2=50" splits users between versions of the RAG prompt (app/prompts/registry.py)
PROMPT_AB = os.getenv("PROMPT_AB", "")

# PII redaction: off | index (before chunks are stored) | llm (before text is sent to the model) | both
PII_REDACTION = os.getenv("PII_REDACTION", "off").lower()
PII_ENGINE = os.getenv("PII_ENGINE", "regex").lower()  # regex | presidio

# SSO (OpenID Connect): Entra ID, Google, Okta, Keycloak, ...
# Entra ID (app/core/entra.py): a tenant id, or "organizations" for any work account (then list the allowed tenants)
ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")
ENTRA_ALLOWED_TENANTS = {t.strip() for t in os.getenv("ENTRA_ALLOWED_TENANTS", "").split(",") if t.strip()}
ENTRA_GROUP_NAMES = os.getenv("ENTRA_GROUP_NAMES", "")  # "<group object id>=hr,<id>=finance"
# with ENTRA_TENANT_ID set, the issuer follows from it
OIDC_ISSUER = os.getenv("OIDC_ISSUER") or (
    f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/v2.0" if ENTRA_TENANT_ID else "")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.getenv("OIDC_REDIRECT_URI", "http://localhost:8080/api/auth/oidc/callback")
OIDC_GROUPS_CLAIM = os.getenv("OIDC_GROUPS_CLAIM", "groups")  # its values become the user's groups (ACL)

# Redis: shared, fast counters for the rate limits instead of PostgreSQL. Empty = PostgreSQL.
REDIS_URL = os.getenv("REDIS_URL", "")

# Event-driven ingestion: S3/MinIO bucket notifications to POST /ingest/events/s3, with this bearer token
INGEST_WEBHOOK_TOKEN = os.getenv("INGEST_WEBHOOK_TOKEN", "")
# ... or through Kafka (python -m app.ingestion.kafka_consumer): MinIO/S3 publishes, a consumer group reads
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "")  # e.g. kafka:9092; empty = the consumer refuses to start
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "bucket-events")
KAFKA_GROUP = os.getenv("KAFKA_GROUP", "ingest")  # consumers in one group share the topic's partitions
KAFKA_DLQ_TOPIC = os.getenv("KAFKA_DLQ_TOPIC", "bucket-events.dlq")  # events that kept failing
KAFKA_MAX_ATTEMPTS = int(os.getenv("KAFKA_MAX_ATTEMPTS", "5"))
# ... or from a local/network folder (python -m app.ingestion.folder_watcher <folder>): file system events.
# A file is queued once it has been quiet this long: one save or copy fires several events.
WATCH_DEBOUNCE_SECONDS = float(os.getenv("WATCH_DEBOUNCE_SECONDS", "2"))

# ---------- Advanced (docs/advanced.md). All off by default. ----------

# Model routing: easy questions to the default model, hard ones to a larger one (app/clients/router.py)
LLM_ROUTER = os.getenv("LLM_ROUTER", "off").lower()  # off | heuristic | llm
ROUTER_LARGE_MODEL = os.getenv("ROUTER_LARGE_MODEL", "")  # an Ollama model, or a vLLM model with VLLM_URL
VLLM_URL = os.getenv("VLLM_URL", "")  # OpenAI-compatible endpoint, e.g. http://vllm:8000/v1
# Multimodal: a vision model on Ollama (e.g. llava:7b, qwen2.5vl:7b) describes images in PDFs and image files
VISION_MODEL = os.getenv("VISION_MODEL", "")
# Agent-to-agent (A2A): publish the agent at /a2a for other agents to call, with this bearer token
A2A_TOKEN = os.getenv("A2A_TOKEN", "")
# MCP over HTTP (python -m app.mcp_server --http): clients must send this bearer token
MCP_HTTP_TOKEN = os.getenv("MCP_HTTP_TOKEN", "")
# Data retention (python -m app.maintenance.retention): delete older data; 0 = keep forever
RETENTION_CHAT_DAYS = int(os.getenv("RETENTION_CHAT_DAYS", "0"))
RETENTION_LOG_DAYS = int(os.getenv("RETENTION_LOG_DAYS", "0"))  # usage, prompt runs, feedback

for _name, _allowed in [("RERANK", "off cross_encoder api"), ("QUERY_REWRITE", "off condense multi hyde"),
                        ("PII_REDACTION", "off index llm both"), ("PII_ENGINE", "regex presidio"),
                        ("AGENT_CHECKPOINTER", "memory postgres"),
                        ("LLM_ROUTER", "off heuristic llm")]:
    if globals()[_name] not in _allowed.split():
        raise ValueError(f"{_name} must be one of: {_allowed.replace(' ', ', ')}")

_DEV_MINIO_SECRET = "minioadmin123"


def validate() -> None:
    """Fail fast at startup: in production, a weak or missing setting is a crash, not a warning."""
    if not IS_PRODUCTION:
        return
    problems = []
    if len(JWT_SECRET) < 32:
        problems.append("JWT_SECRET must be set to a random string of at least 32 characters")
    if MINIO_SECRET_KEY == _DEV_MINIO_SECRET:
        problems.append("MINIO_SECRET_KEY still has the development default")
    if "postgres:postgres@" in DATABASE_URL:
        problems.append("DATABASE_URL uses the development password")
    if not COOKIE_SECURE:
        problems.append("COOKIE_SECURE must be true (sessions over https only)")
    if INGEST_WEBHOOK_TOKEN and len(INGEST_WEBHOOK_TOKEN) < 32:
        problems.append("INGEST_WEBHOOK_TOKEN must be at least 32 random characters")
    if problems:
        raise RuntimeError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
