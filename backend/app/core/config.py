"""Settings, read from environment variables (see .env.example for the full list)."""

import os

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:1b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# Qdrant
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6335")
# v2: new chunk format + nomic task prefixes, so vectors aren't comparable with the old collection
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "documents_v2")
# If even the best chunk scores below this, the question isn't about the documents.
# Measured with nomic-embed-text + prefixes: related questions >= ~0.60, unrelated <= ~0.57.
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.58"))

# PostgreSQL (app data: eval results)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/mychatapp")

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
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
