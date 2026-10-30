"""Batch ingestion queue: ingest_batches table and queue columns on documents.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_batches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.add_column("documents", sa.Column(
        "batch_id", sa.Integer, sa.ForeignKey("ingest_batches.id", ondelete="SET NULL")))
    op.add_column("documents", sa.Column("attempts", sa.Integer, nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("worker", sa.String(64)))
    op.add_column("documents", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("error", sa.Text))
    op.create_index("ix_documents_batch_id", "documents", ["batch_id"])
    # workers look for queued/processing rows many times a second; keep that lookup small
    op.create_index("ix_documents_batch_pending", "documents", ["batch_id", "status"],
                    postgresql_where=sa.text("status IN ('queued', 'processing')"))


def downgrade() -> None:
    op.drop_index("ix_documents_batch_pending", table_name="documents")
    op.drop_index("ix_documents_batch_id", table_name="documents")
    for col in ("error", "claimed_at", "worker", "attempts", "batch_id"):
        op.drop_column("documents", col)
    op.drop_table("ingest_batches")
