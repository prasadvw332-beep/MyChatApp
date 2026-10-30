"""Tighten the schema: required chat owner, ordered messages, unique document hash, timestamptz.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # chats saved before logins have no owner: give them to the first user, or drop them if none
    op.execute("UPDATE chats SET user_id = (SELECT min(id) FROM users) WHERE user_id IS NULL")
    op.execute("DELETE FROM chats WHERE user_id IS NULL")
    op.alter_column("chats", "user_id", nullable=False)

    op.create_unique_constraint("uq_messages_chat_position", "messages", ["chat_id", "position"])

    # one row per file content; the old non-unique index is replaced by a unique one
    op.execute("DROP INDEX IF EXISTS ix_documents_sha256")
    op.create_index("uq_documents_sha256", "documents", ["sha256"], unique=True)

    op.execute("ALTER TABLE eval_runs ALTER COLUMN created_at TYPE timestamptz "
               "USING created_at AT TIME ZONE current_setting('TimeZone')")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs ALTER COLUMN created_at TYPE timestamp "
               "USING created_at AT TIME ZONE current_setting('TimeZone')")
    op.drop_index("uq_documents_sha256", table_name="documents")
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.drop_constraint("uq_messages_chat_position", "messages")
    op.alter_column("chats", "user_id", nullable=True)
