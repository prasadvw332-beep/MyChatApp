"""Clients for the external services: Ollama (llm.py), MinIO (storage.py), Qdrant (vector_store.py).

Only these modules know how to talk to those servers; swapping one (Ollama -> a hosted API, MinIO -> S3)
means changing one file here."""
