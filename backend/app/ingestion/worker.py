"""Ingestion worker: takes queued documents one by one and indexes them.

    uv run python -m app.ingestion.worker

Run as many as you like (deploy/k8s/worker-deployment.yaml runs 3); they share the queue in PostgreSQL
(see queue.py). On SIGTERM (Kubernetes scaling down or rolling out a new version)
the worker finishes its current document before exiting, so no work is thrown away.
"""

import logging
import os
import signal
import socket
import time

from app.clients import storage
from app.core import config, metrics, telemetry
from app.core.config import INGEST_MAX_ATTEMPTS
from app.core.logging import set_request_id, setup_logging
from app.ingestion import queue
from app.rag import knowledge_base
from app.rag.chunking import UnreadableDocument

log = logging.getLogger("worker")

NAME = os.getenv("HOSTNAME") or socket.gethostname()  # the pod name in Kubernetes
IDLE_SECONDS = 2  # how long to wait before asking again when there is nothing to claim

_stopping = False
_FAIL_OUTCOME = {"queued": "retry", "failed": "failed"}  # queue.fail()'s new status -> metric label


def _stop(signum, _frame) -> None:
    global _stopping
    _stopping = True
    log.info("got %s, stopping after the current document", signal.Signals(signum).name)


def process(doc) -> None:
    # one trace per document: its Ollama, Qdrant and PostgreSQL calls are the child spans
    with telemetry.span("ingest.document", doc_id=doc.doc_id, filename=doc.filename, attempt=doc.attempts,
                        batch_id=doc.batch_id):
        _process(doc)


def _process(doc) -> None:
    if doc.attempts > INGEST_MAX_ATTEMPTS:  # its earlier workers died or ran past the lease
        queue.fail(doc, doc.error or "Gave up: processing never completed.", retry=False)
        metrics.ingest_documents.labels("failed").inc()
        return
    started = time.monotonic()
    set_request_id(f"doc-{doc.doc_id[:12]}")  # every log line about this document carries its id
    try:
        chunks = knowledge_base.index(doc.doc_id, doc.filename, storage.read(doc.minio_key)[0])
    except UnreadableDocument as e:  # retrying won't change that
        log.warning("failed %s: %s", doc.filename, e)
        queue.fail(doc, str(e), retry=False)
        metrics.ingest_documents.labels("failed").inc()
        return
    except Exception as e:  # Ollama/Qdrant/MinIO hiccup: try again later
        status = queue.fail(doc, f"{type(e).__name__}: {e}")
        if outcome := _FAIL_OUTCOME.get(status):  # not counted: deleted while we worked on it
            metrics.ingest_documents.labels(outcome).inc()
        log.warning("attempt %d/%d failed for %s (%s): %s",
                    doc.attempts, INGEST_MAX_ATTEMPTS, doc.filename, status, e)
        return
    if not queue.finish(doc, chunks):  # deleted meanwhile: drop what we just indexed
        knowledge_base.delete_document(doc.doc_id)
        return
    for old in queue.older_versions(doc):
        knowledge_base.delete_document(old)
    metrics.ingest_documents.labels("indexed").inc()
    metrics.ingest_duration.observe(time.monotonic() - started)
    metrics.ingest_chunks.inc(chunks)
    log.info("indexed %s: %d chunks in %.1fs (batch %s)", doc.filename, chunks, time.monotonic() - started, doc.batch_id)


def main() -> None:
    setup_logging()
    config.validate()
    telemetry.setup_tracing("mychatapp-worker")
    metrics.start_server()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker %s started", NAME)
    while not _stopping:
        try:
            doc = queue.claim(NAME)
            if doc is None:
                time.sleep(IDLE_SECONDS)
                continue
            process(doc)
        except Exception as e:
            # e.g. PostgreSQL restarting. A claimed document stays 'processing' and is
            # picked up again once its lease expires.
            log.exception("worker loop error: %s", e)
            time.sleep(5)
    telemetry.shutdown()
    log.info("worker %s stopped", NAME)


if __name__ == "__main__":
    main()
