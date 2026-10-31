"""Caching (docs/caching.md). Both caches are best-effort: when one fails, the request goes ahead without it.

    embeddings.py  the same text is embedded once: an LRU per process, then PostgreSQL (shared, persistent)
    semantic.py    a first question close enough to one already answered gets that answer, without retrieval
                   or the chat model; emptied whenever a document is added or removed
"""
