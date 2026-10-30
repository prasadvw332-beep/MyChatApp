"""Database tables (SQLAlchemy), one module per area. Everything is imported here so that
`from app.models import Document` works and Alembic sees every table."""

from app.models.chat import Chat, Message
from app.models.document import Document, IngestBatch
from app.models.evaluation import EvalRun
from app.models.user import User

__all__ = ["Chat", "Document", "EvalRun", "IngestBatch", "Message", "User"]
