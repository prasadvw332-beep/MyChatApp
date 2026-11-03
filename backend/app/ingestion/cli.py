"""Bulk import: queue every PDF/DOCX/TXT/MD file of a source, in batches, for the workers.

    uv run python -m app.ingestion.cli ./my-documents                 # batches of INGEST_BATCH_SIZE (100)
    uv run python -m app.ingestion.cli s3://company-docs/hr           # an S3 / MinIO bucket
    uv run python -m app.ingestion.cli gdrive://<folder id>           # other sources: see connectors/__init__.py
    uv run python -m app.ingestion.cli ./my-documents --batch-size 50
    uv run python -m app.ingestion.cli --status                       # progress of the batches
    uv run python -m app.ingestion.cli --failures 3                   # why documents in batch 3 failed
    uv run python -m app.ingestion.cli --retry-failed                 # queue failed documents again

This only stores files and creates jobs, which is fast; the workers (python -m app.ingestion.worker) do the
slow part. Files are stored under their path relative to the source ("hr/policy.pdf"), so files
with the same name in different subfolders stay separate, and importing a changed file again
replaces the old version once the new one is indexed.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import batched
from pathlib import Path, PurePosixPath

from app.core.config import INGEST_BATCH_SIZE, MAX_UPLOAD_MB
from app.ingestion import connectors, queue
from app.ingestion.connectors import SourceFile
from app.rag.chunking import SUPPORTED


def find_files(source: str) -> list[SourceFile]:
    files, too_big = [], 0
    for f in connectors.list_files(source):
        if PurePosixPath(f.name).suffix.lower() not in SUPPORTED:
            continue
        if f.size > MAX_UPLOAD_MB * 1024 * 1024:
            too_big += 1
            continue
        files.append(f)
    if too_big:
        print(f"skipping {too_big} file(s) larger than {MAX_UPLOAD_MB} MB")
    return files


def enqueue(source: str, batch_size: int, threads: int) -> None:
    files = find_files(source)
    print(f"{len(files)} file(s) in {source}, batches of {batch_size}")
    queued = skipped = 0
    # downloading from the source and uploading to MinIO are network-bound, so several threads speed it up a lot
    with ThreadPoolExecutor(threads) as pool:
        for group in batched(files, batch_size, strict=False):  # the last batch may be smaller
            batch_id = queue.create_batch(source)
            try:
                results = list(pool.map(
                    lambda f, batch_id=batch_id: queue.add(batch_id, f.name, f.read()),
                    group,
                ))
            finally:
                queue.seal(batch_id)  # even after an error: an unsealed batch would block the queue
            added = sum(r is not None for r in results)
            queued, skipped = queued + added, skipped + len(group) - added
            print(f"batch {batch_id}: queued {added}/{len(group)}  ({queued + skipped}/{len(files)} files)")
    print(f"done: {queued} queued, {skipped} skipped as duplicates. Watch progress with --status")


def show_status() -> None:
    p = queue.progress()
    t = p["totals"]
    total = sum(t.values())
    done = t["ready"] + t["failed"]
    print(f"overall: {done}/{total} done  (ready {t['ready']}, failed {t['failed']}, "
          f"processing {t['processing']}, queued {t['queued']})")
    print(f"active workers: {', '.join(p['active_workers']) or '-'}")
    print(f"\n{'batch':>6} {'status':<8} {'ready':>6} {'failed':>6} {'busy':>5} {'queued':>6} {'total':>6}")
    for b in p["batches"]:
        print(f"{b['id']:>6} {b['status']:<8} {b['ready']:>6} {b['failed']:>6} {b['processing']:>5} {b['queued']:>6} {b['total']:>6}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="folder to import (searched recursively) or a source URL: s3://, sql://, https://, ... (see connectors/__init__.py)")
    ap.add_argument("--batch-size", type=int, default=INGEST_BATCH_SIZE)
    ap.add_argument("--threads", type=int, default=8, help="parallel uploads to MinIO")
    ap.add_argument("--status", action="store_true", help="show batch progress")
    ap.add_argument("--failures", type=int, metavar="BATCH", help="list failed documents of a batch")
    ap.add_argument("--retry-failed", action="store_true", help="queue all failed documents again")
    args = ap.parse_args()

    if args.status:
        show_status()
    elif args.failures is not None:
        for f in queue.failures(args.failures):
            print(f"{f['filename']}  (attempts {f['attempts']})\n    {f['error']}")
    elif args.retry_failed:
        print(f"queued {queue.retry_failed()} failed document(s) again")
    elif args.source:
        # a folder is recorded as its absolute path in the batch's source column, as before
        source = args.source if "://" in args.source else str(Path(args.source).resolve())
        try:
            enqueue(source, args.batch_size, args.threads)
        except ValueError as e:  # bad folder, bad s3:// url, missing bucket
            sys.exit(str(e))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
