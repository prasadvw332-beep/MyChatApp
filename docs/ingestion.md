# Batch ingestion

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

The same is available over HTTP: `POST /ingest/batches` (multi-file upload), `GET /ingest/batches`, `GET /ingest/batches/{id}/failures`, `POST /ingest/retry-failed`. In Kubernetes the workers are `deploy/k8s/base/worker-deployment.yaml` (3 replicas).

The throughput limit is embedding, not the number of workers: all workers share one Ollama. `OLLAMA_NUM_PARALLEL=3` lets it serve them at the same time. Beyond that, more speed needs more Ollama replicas or a GPU.
