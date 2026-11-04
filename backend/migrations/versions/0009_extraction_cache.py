"""Ingestion caches: extracted PDF page text (extraction_cache), graph triples per chunk (graph_cache).

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("extractor", sa.String(120), nullable=False),
        sa.Column("text", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "graph_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("triples", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("graph_cache")
    op.drop_table("extraction_cache")
