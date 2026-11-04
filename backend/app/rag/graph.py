"""GraphRAG, small: a knowledge graph of entities and relations, next to the vectors (GRAPH_RAG=true).

Vector search finds chunks that look like the question. It struggles when the answer is spread
over documents: "Who manages the people working on Project Atlas?" needs "Ravi -works_on->
Atlas" from one file and "Meera -manages-> Ravi" from another; neither chunk resembles the
question. A graph connects them.

    index time   the LLM reads each chunk and lists (entity, relation, entity) triples
                 -> graph_entities / graph_relations in PostgreSQL, tagged with the document
    query time   entities named in the question -> their relations, 2 hops out
                 -> added to the prompt as "facts", next to the retrieved chunks

Cost: one model call per chunk while indexing (the workers do it, not the chat request); with
GRAPH_CACHE, re-indexing an edited document only asks about the chunks whose text changed. Small
models extract noisy triples; production setups use a stronger model for this step, or
Microsoft's GraphRAG, which also summarises communities of entities. A graph database (Neo4j)
replaces the two tables when the graph grows large or needs deeper traversals.
Relations are deleted with their document (foreign key, ON DELETE CASCADE).
"""

import hashlib
import json
import logging

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.clients.llm import utility_llm
from app.core import metrics
from app.core.config import GRAPH_CACHE, GRAPH_RAG
from app.core.db import SessionLocal
from app.models import GraphEntity, GraphRelation
from app.repositories import extraction_cache_store

log = logging.getLogger(__name__)

# each chunk's triples, keyed by what the model saw; None = always ask the model
cache = extraction_cache_store.triples if GRAPH_CACHE else None
MAX_CHARS = 4000  # of a chunk sent to the model

EXTRACT = (
    "Extract the important entities (people, teams, organisations, products, projects, places, "
    "policies) and how they relate, from the text. Answer with JSON only:\n"
    '{"entities": [{"name": "...", "type": "person|team|org|product|project|place|policy|other"}], '
    '"relations": [{"source": "...", "relation": "works_on|manages|part_of|located_in|owns|uses|...", '
    '"target": "..."}]}\n'
    "Use the names exactly as written. No relation without both entities. At most 10 relations."
)
MAX_FACTS = 30


def is_enabled() -> bool:
    return GRAPH_RAG


def _parse(text: str) -> dict:
    """The model's JSON, tolerating text around it (small models add some)."""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if start >= 0 else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def extract(text: str) -> dict:
    out = utility_llm.invoke([SystemMessage(EXTRACT), HumanMessage(text[:MAX_CHARS])])
    return _parse(out.content or "")


def _cache_key(text: str) -> str:
    """Model, prompt and text: another model or an edited prompt reads the chunk again."""
    return hashlib.sha256(f"{utility_llm.model}\0{EXTRACT}\0{text[:MAX_CHARS]}".encode()).hexdigest()


def _load_cached(docs: list[Document]) -> dict[str, dict]:
    """The cached triples of these chunks, in one query. Never raises: a cache failure only costs model calls."""
    if not cache:
        return {}
    try:
        return cache.get_many(list({_cache_key(d.page_content) for d in docs}))
    except Exception as e:
        metrics.cache_errors.labels("graph").inc()
        log.warning("graph cache read failed, asking the model for every chunk: %s", e)
        return {}


def _extract_cached(text: str, cached: dict[str, dict]) -> dict:
    key = _cache_key(text)
    if key in cached:
        metrics.graph_cache.labels("hit").inc()
        return cached[key]
    metrics.graph_cache.labels("miss").inc()
    data = extract(text)
    # {} is a reply that wasn't JSON: worth asking again next time, not remembering
    if cache and data:
        try:
            cache.put_many(utility_llm.model, {key: data})
        except Exception as e:
            metrics.cache_errors.labels("graph").inc()
            log.warning("graph cache write failed: %s", e)
    cached[key] = data  # the same text twice in one document is asked once
    return data


def forget(docs: list[Document]) -> None:
    """Remove these chunks' cached triples (personal data erasure). Raises if that fails."""
    if cache:
        cache.delete_many([_cache_key(d.page_content) for d in docs])


def _entity_id(db, name: str, type_: str) -> int:
    key = name.strip().lower()[:200]
    stmt = insert(GraphEntity).values(name=key, label=name.strip()[:200], type=(type_ or "other")[:40])
    db.execute(stmt.on_conflict_do_nothing(index_elements=["name"]))
    return db.scalar(select(GraphEntity.id).where(GraphEntity.name == key))


def index(doc_id: str, docs: list[Document]) -> int:
    """Extract and store the triples of one document's chunks. Returns how many relations were stored.
    Never raises: the vectors are what search needs, the graph is extra."""
    stored = 0
    cached = _load_cached(docs)
    for doc in docs:
        try:
            data = _extract_cached(doc.page_content, cached)
            types = {e.get("name", "").strip().lower(): e.get("type", "other") for e in data.get("entities", [])}
            with SessionLocal.begin() as db:
                for r in data.get("relations", [])[:10]:
                    source, target, relation = r.get("source", ""), r.get("target", ""), r.get("relation", "")
                    if not (source.strip() and target.strip() and relation.strip()):
                        continue
                    stmt = insert(GraphRelation).values(
                        source_id=_entity_id(db, source, types.get(source.strip().lower(), "other")),
                        relation=relation.strip().lower().replace(" ", "_")[:80],
                        target_id=_entity_id(db, target, types.get(target.strip().lower(), "other")),
                        doc_id=doc_id,
                    )
                    db.execute(stmt.on_conflict_do_nothing(constraint="uq_graph_relation"))
                    stored += 1
        except Exception:
            log.exception("graph extraction failed for a chunk of %s", doc_id)
    return stored


def find_facts(question: str, hops: int = 2) -> list[str]:
    """Relations around the entities the question names, as lines: "Ravi -works_on-> Project Atlas"."""
    q = question.lower()
    with SessionLocal() as db:
        # entities whose name appears in the question (names of 3+ characters, to skip noise like "it")
        frontier = set(db.scalars(select(GraphEntity.id).where(
            func.length(GraphEntity.name) >= 3, func.strpos(q, GraphEntity.name) > 0)))
        seen, lines = set(frontier), []
        for _ in range(hops):
            if not frontier:
                break
            rows = db.execute(
                select(GraphRelation.source_id, GraphRelation.relation, GraphRelation.target_id)
                .where(or_(GraphRelation.source_id.in_(frontier), GraphRelation.target_id.in_(frontier)))
                .distinct().limit(MAX_FACTS)
            ).all()
            labels = dict(db.execute(select(GraphEntity.id, GraphEntity.label).where(
                GraphEntity.id.in_({r[0] for r in rows} | {r[2] for r in rows}))).all())
            next_frontier = set()
            for source, relation, target in rows:
                lines.append(f"{labels[source]} -{relation}-> {labels[target]}")
                next_frontier |= {source, target} - seen
            seen |= next_frontier
            frontier = next_frontier
    return list(dict.fromkeys(lines))[:MAX_FACTS]
