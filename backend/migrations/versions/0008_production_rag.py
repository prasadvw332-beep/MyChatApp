"""Production RAG features: document ACLs, user groups and SSO id, agent memory, knowledge graph,
prompt experiments.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # existing documents stay visible to everyone: ACLs only narrow what is uploaded from now on
    op.add_column("documents", sa.Column("acl", postgresql.JSONB, nullable=False, server_default='["public"]'))
    # GIN index: "acl contains any of these principals" without scanning every row
    op.create_index("ix_documents_acl", "documents", ["acl"], postgresql_using="gin")
    op.add_column("users", sa.Column("groups", postgresql.JSONB, nullable=False, server_default="[]"))
    op.add_column("users", sa.Column("oidc_subject", sa.String(255), unique=True))

    op.create_table(
        "user_memories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_memories_user_id", "user_memories", ["user_id"])

    op.create_table(
        "graph_entities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
    )
    op.create_table(
        "graph_relations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("graph_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(80), nullable=False),
        sa.Column("target_id", sa.Integer, sa.ForeignKey("graph_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_id", sa.String(32), sa.ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("source_id", "relation", "target_id", "doc_id", name="uq_graph_relation"),
    )
    for col in ("source_id", "target_id", "doc_id"):
        op.create_index(f"ix_graph_relations_{col}", "graph_relations", [col])

    op.create_table(
        "prompt_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("prompt", sa.String(32), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prompt_runs_request_id", "prompt_runs", ["request_id"])
    op.create_index("ix_prompt_runs_created_at", "prompt_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("prompt_runs")
    op.drop_table("graph_relations")
    op.drop_table("graph_entities")
    op.drop_table("user_memories")
    op.drop_column("users", "oidc_subject")
    op.drop_column("users", "groups")
    op.drop_index("ix_documents_acl", "documents")
    op.drop_column("documents", "acl")
