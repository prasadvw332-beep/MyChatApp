"""Tables of the production RAG features: agent memory, knowledge graph, prompt experiments."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserMemory(Base):
    """Something the agent was asked to remember about a user ("I work in the Pune office").
    Long-term: it outlives the chat it was said in (app/agents/memory.py)."""

    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GraphEntity(Base):
    """A thing the documents talk about (person, team, product, place), one row per name (app/rag/graph.py)."""

    __tablename__ = "graph_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)  # lower-case: "asha rao"
    label: Mapped[str] = mapped_column(String(200))  # as written: "Asha Rao"
    type: Mapped[str] = mapped_column(String(40))


class GraphRelation(Base):
    """"Asha Rao -works_in-> Pune office", and the document it was read from (deleted with it)."""

    __tablename__ = "graph_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("graph_entities.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[int] = mapped_column(ForeignKey("graph_entities.id", ondelete="CASCADE"), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.doc_id", ondelete="CASCADE"), index=True)

    __table_args__ = (UniqueConstraint("source_id", "relation", "target_id", "doc_id", name="uq_graph_relation"),)


class PromptRun(Base):
    """Which prompt version answered a request. Joined with feedback on request_id, it gives the
    thumbs-up rate per version: the result of an A/B test (app/prompts/registry.py)."""

    __tablename__ = "prompt_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    prompt: Mapped[str] = mapped_column(String(32))  # "rag"
    version: Mapped[str] = mapped_column(String(16))  # "v1"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
