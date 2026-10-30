"""Baseline: the schema as it was before migrations existed.

Databases created by the old create_all() startup already have these tables, so every
step checks first. Fresh databases get the tables created here.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" not in have:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("username", sa.String(32), nullable=False, unique=True),
            sa.Column("password_hash", sa.String, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "chats" not in have:
        op.create_table(
            "chats",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("title", sa.String, nullable=False),
            sa.Column("title_by", sa.String(8)),
            sa.Column("titled_at", sa.Integer),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_chats_user_id", "chats", ["user_id"])
        op.create_index("ix_chats_updated_at", "chats", ["updated_at"])
    if "messages" not in have:
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("chat_id", sa.String(64), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer, nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("ui", sa.String(16)),
            sa.Column("sources", JSONB),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_messages_chat_id", "messages", ["chat_id"])
    if "documents" not in have:
        op.create_table(
            "documents",
            sa.Column("doc_id", sa.String(32), primary_key=True),
            sa.Column("filename", sa.String, nullable=False),
            sa.Column("ext", sa.String(8), nullable=False),
            sa.Column("size_bytes", sa.BigInteger, nullable=False),
            sa.Column("sha256", sa.String(64)),
            sa.Column("chunks", sa.Integer, nullable=False),
            sa.Column("minio_key", sa.String),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("tags", JSONB, nullable=False),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_documents_sha256", "documents", ["sha256"])
    if "eval_runs" not in have:
        op.create_table(
            "eval_runs",
            sa.Column("id", sa.String(15), primary_key=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("duration_s", sa.Integer, nullable=False),
            sa.Column("chat_model", sa.String, nullable=False),
            sa.Column("judge_model", sa.String, nullable=False),
            sa.Column("metrics", JSONB, nullable=False),
            sa.Column("summary", JSONB, nullable=False),
            sa.Column("scored", JSONB, nullable=False),
            sa.Column("no_context", sa.Integer, nullable=False),
            sa.Column("rows", JSONB, nullable=False),
        )


def downgrade() -> None:
    for table in ("eval_runs", "documents", "messages", "chats", "users"):
        op.drop_table(table)
