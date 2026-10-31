# 0002. PostgreSQL as the ingestion queue

- **Status:** accepted
- **Date:** 2026-10-31

## Context
Loading thousands of documents needs a queue: several workers, retries, a dead worker's document taken
over, progress per batch. The usual answer is a broker (RabbitMQ, Redis, SQS).

## Decision
Use the **`documents` table in PostgreSQL** as the queue: workers claim rows with
`SELECT ... FOR UPDATE SKIP LOCKED`, a lease (`INGEST_LEASE_SECONDS`) lets another worker take over, and
attempts are counted per row (`backend/app/ingestion/queue.py`).

## Consequences
- No extra service to run, back up and monitor; the queue state *is* the document's status, so the UI
  and the CLI read progress with plain SQL, and nothing can get out of sync between a broker and the DB.
- Fine for thousands of documents per batch; at very high rates (many thousands per second) a dedicated
  broker would be the better tool. The real bottleneck today is embedding, not the queue.
