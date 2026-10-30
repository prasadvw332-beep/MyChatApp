"""Batch ingestion pipeline: load thousands of documents with a pool of workers.

    queue.py   the queue in PostgreSQL: batches, claiming, retries, progress
    worker.py  python -m app.ingestion.worker   (run several; k8s runs 3)
    cli.py     python -m app.ingestion.cli FOLDER   (queue a folder; --status, --retry-failed)

The HTTP endpoints are in app/api/routes/ingest.py; indexing one document is app.rag.knowledge_base.index().
"""
