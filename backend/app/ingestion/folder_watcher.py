"""Folder events -> the ingestion queue: what kafka_consumer.py does for a bucket, for a local or network folder.

    uv run python -m app.ingestion.folder_watcher ./my-documents

A file saved, copied, renamed or deleted in the folder is queued (or deleted) within seconds, and the
workers index it as usual. This process only notices changes; it doesn't extract, chunk or embed anything.

    watchdog (OS file events) ─► debounce ─► queue.add() ─► PostgreSQL queue ─► worker.py indexes it
                                         └─► knowledge_base.delete_document() for deleted files

Problems every file watcher has, and how they're handled here:
    half-written  one save or copy fires several "modified" events; reading after the first would queue half
                  a file. A file is handled once it has been quiet for WATCH_DEBOUNCE_SECONDS.
    duplicates    events are only hints: when one is due we look at the file as it is now. Unchanged content
                  is skipped by its sha256 (queue.add), and deleting what's already gone deletes nothing.
    downtime      changes made while the watcher was stopped have no events. At startup the whole folder is
                  queued once (unchanged files are skipped by hash), like `cli <folder>`.
    missed events the OS can drop events (overflowing buffers, network shares, a whole folder moved at once),
                  so this isn't the only line of defence: the nightly `cli <folder> --wait --sync` (Airflow,
                  deploy/airflow/dags/ingest_sources.py) compares everything and fixes whatever slipped through.
    one instance  every watcher on the same folder would queue the same files: run exactly one (Kubernetes:
                  replicas: 1, strategy Recreate). It scales by the workers behind the queue, not by watchers.

Documents are named by their path relative to the folder ("hr/policy.pdf") and recorded under the folder's
absolute path as their source, both exactly like `cli <folder>`, so --sync recognises them.
"""

import argparse
import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core import config, telemetry
from app.core.config import INGEST_BATCH_SIZE, MAX_UPLOAD_MB, WATCH_DEBOUNCE_SECONDS
from app.core.logging import set_request_id, setup_logging
from app.ingestion import cli, queue
from app.rag import knowledge_base
from app.rag.chunking import SUPPORTED
from app.repositories import doc_store

log = logging.getLogger("folder-watcher")

_stopping = threading.Event()


def _stop(signum, _frame) -> None:
    _stopping.set()
    log.info("got %s, stopping after the current changes", signal.Signals(signum).name)


def is_ingestible(path: Path) -> bool:
    # editors and office apps write lock and temp files next to the document while it's open
    return path.suffix.lower() in SUPPORTED and not path.name.startswith(("~$", "."))


class Debouncer:
    """Collects paths and releases each one once it has been quiet for `quiet_seconds`."""

    def __init__(self, quiet_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.quiet_seconds = quiet_seconds
        self._clock = clock
        self._last_seen: dict[Path, float] = {}
        self._lock = threading.Lock()  # touched from watchdog's thread, drained from the main one

    def touch(self, path: Path) -> None:
        with self._lock:
            self._last_seen[path] = self._clock()

    def due(self) -> list[Path]:
        with self._lock:
            now = self._clock()
            ready = [p for p, t in self._last_seen.items() if now - t >= self.quiet_seconds]
            for p in ready:
                del self._last_seen[p]
            return ready


class _FolderEventHandler(FileSystemEventHandler):
    """Runs on watchdog's thread: only notes the path, so the OS event buffer is never kept waiting."""

    def __init__(self, debouncer: Debouncer) -> None:
        self.debouncer = debouncer

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in ("created", "modified", "deleted", "moved"):
            return
        # a rename is a delete of the old name plus a create of the new one
        for raw in (event.src_path, getattr(event, "dest_path", "")):
            if raw and is_ingestible(Path(raw)):
                self.debouncer.touch(Path(raw))


def apply_changes(folder: Path, changed: list[Path]) -> dict:
    """Queue the files that exist now, delete the documents of those that don't. Returns counts."""
    source = str(folder)
    queued = skipped = deleted = 0
    deleted_names: list[str] = []
    batch_id = None
    try:
        for path in changed:
            name = path.relative_to(folder).as_posix()
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                deleted_names.append(name)
                continue
            except OSError as e:  # e.g. still locked by the program writing it: the next event or --sync retries
                log.warning("skipping %s: %s", name, e)
                skipped += 1
                continue
            if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
                log.warning("skipping %s: larger than %s MB", name, MAX_UPLOAD_MB)
                skipped += 1
                continue
            if batch_id is None:  # one batch per burst of changes, only when there's something to queue
                batch_id = queue.create_batch(source)
            if queue.add(batch_id, name, data) is None:
                skipped += 1  # same content already queued or indexed: a repeat event or an unchanged save
            else:
                queued += 1
    finally:
        if batch_id is not None:
            queue.seal(batch_id)  # even after an error: an unsealed batch would block the queue
    for name in deleted_names:
        for doc_id in doc_store.ids_for_filename(name):
            knowledge_base.delete_document(doc_id)
            deleted += 1
    return {"queued": queued, "skipped": skipped, "deleted": deleted}


def watch(folder: Path, poll_seconds: float = 0.5) -> None:
    debouncer = Debouncer(WATCH_DEBOUNCE_SECONDS)
    observer = Observer()
    observer.schedule(_FolderEventHandler(debouncer), str(folder), recursive=True)
    observer.start()  # before the catch-up, so a change made during it isn't missed
    log.info("watching %s", folder)
    try:
        # catch up on what changed while we were stopped; deletions wait for the nightly --sync, which
        # has the safety limits (an unmounted share must not look like "every file was deleted")
        cli.enqueue(str(folder), INGEST_BATCH_SIZE, threads=4)
        while not _stopping.wait(poll_seconds):
            changed = debouncer.due()
            if not changed:
                continue
            set_request_id(f"watch-{int(time.time())}")
            try:
                with telemetry.span("ingest.folder_changes", files=len(changed)):
                    log.info("changes: %s", apply_changes(folder, changed))
            except Exception:
                # PostgreSQL or MinIO briefly down: give the files another go after the next quiet period
                log.exception("could not queue %d changed file(s); retrying", len(changed))
                for path in changed:
                    debouncer.touch(path)
    finally:
        observer.stop()
        observer.join()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder to watch, subfolders included")
    args = ap.parse_args()
    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {args.folder}")

    setup_logging()
    config.validate()
    telemetry.setup_tracing("mychatapp-folder-watcher")
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _stop)
    try:
        watch(folder)
    finally:
        telemetry.shutdown()
        log.info("watcher stopped")


if __name__ == "__main__":
    main()
