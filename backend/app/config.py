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

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "documents")

# API
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
MAX_UPLOAD_MB = 20
