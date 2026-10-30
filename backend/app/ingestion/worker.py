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

from app.core.config import INGEST_MAX_ATTEMPTS
from app.ingestion import queue
from app.rag import knowledge_base
from app.services import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

NAME = os.getenv("HOSTNAME") or socket.gethostname()  # the pod name in Kubernetes
IDLE_SECONDS = 2  # how long to wait before asking again when there is nothing to claim

_stopping = False


def _stop(signum, _frame) -> None:
    global _stopping
    _stopping = True
    log.info("got %s, stopping after the current document", signal.Signals(signum).name)


def _download(minio_key: str) -> bytes:
    obj = storage.open_file(minio_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


def process(doc) -> None:
    if doc.attempts > INGEST_MAX_ATTEMPTS:  # its earlier workers died or ran past the lease
        queue.fail(doc, doc.error or "Gave up: processing never completed.", retry=False)
        return
    started = time.monotonic()
    try:
        chunks = knowledge_base.index(doc.doc_id, doc.filename, _download(doc.minio_key))
    except ValueError as e:  # no readable text: retrying won't change that
        log.warning("failed %s: %s", doc.filename, e)
        queue.fail(doc, str(e), retry=False)
        return
    except Exception as e:  # Ollama/Qdrant/MinIO hiccup: try again later
        status = queue.fail(doc, f"{type(e).__name__}: {e}")
        log.warning("attempt %d/%d failed for %s (%s): %s",
                    doc.attempts, INGEST_MAX_ATTEMPTS, doc.filename, status, e)
        return
    if not queue.finish(doc, chunks):  # deleted meanwhile: drop what we just indexed
        knowledge_base.delete_document(doc.doc_id)
        return
    for old in queue.older_versions(doc):
        knowledge_base.delete_document(old)
    log.info("indexed %s: %d chunks in %.1fs (batch %s)", doc.filename, chunks, time.monotonic() - started, doc.batch_id)


def main() -> None:
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
            log.error("worker loop error: %s", e)
            time.sleep(5)
    log.info("worker %s stopped", NAME)


if __name__ == "__main__":
    main()
