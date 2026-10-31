"""Embedding cache (embedding_cache).

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("vector", postgresql.ARRAY(sa.REAL), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("embedding_cache")
