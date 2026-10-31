"""Settings, read from environment variables (see .env.example for the full list)."""

import os

# development | production. Production refuses to start with insecure defaults (see validate()).
APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

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

# Batch ingestion (app/ingestion/)
INGEST_BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "100"))  # documents per batch
INGEST_MAX_ATTEMPTS = int(os.getenv("INGEST_MAX_ATTEMPTS", "3"))  # then the document is marked failed
# a document 'processing' for longer than this is assumed to belong to a dead worker and is retried
INGEST_LEASE_SECONDS = int(os.getenv("INGEST_LEASE_SECONDS", "600"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "64"))  # chunks per embedding request


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
    if problems:
        raise RuntimeError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
