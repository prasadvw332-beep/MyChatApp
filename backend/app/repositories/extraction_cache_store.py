"""Cached page text and graph triples in PostgreSQL (repository: the only module that touches the
extraction_cache and graph_cache tables)."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.models import ExtractionCache, GraphCache


class _Rows:
    """get_many / put_many / delete_many on one cache table; `column` holds the cached value."""

    def __init__(self, model, owner: str, column: str, encode=lambda v: v, decode=lambda v: v):
        self.model, self.owner, self.column = model, owner, column
        self.encode, self.decode = encode, decode

    def get_many(self, keys: list[str]) -> dict:
        query = select(self.model.key, getattr(self.model, self.column)).where(self.model.key.in_(keys))
        with SessionLocal() as db:
            return {k: self.decode(v) for k, v in db.execute(query)}

    def put_many(self, owner: str, values: dict) -> None:
        now = datetime.now(UTC)
        rows = [{"key": k, self.owner: owner, self.column: self.encode(v), "created_at": now} for k, v in values.items()]
        with SessionLocal() as db:
            # two workers extracting the same page at once both insert it: the second one is a no-op
            db.execute(insert(self.model).values(rows).on_conflict_do_nothing(index_elements=["key"]))
            db.commit()

    def delete_many(self, keys: list[str]) -> None:
        with SessionLocal() as db:
            db.execute(delete(self.model).where(self.model.key.in_(keys)))
            db.commit()


# surrogatepass: text from a damaged PDF can hold lone surrogates, which plain UTF-8 refuses
pages = _Rows(ExtractionCache, "extractor", "text",
              encode=lambda t: t.encode("utf-8", "surrogatepass"), decode=lambda b: bytes(b).decode("utf-8", "surrogatepass"))
triples = _Rows(GraphCache, "model", "triples")
