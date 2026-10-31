"""Database tables (SQLAlchemy), one module per area. Everything is imported here so that
`from app.models import Document` works and Alembic sees every table."""

from app.models.chat import Chat, Message
from app.models.document import BatchStatus, DocStatus, Document, IngestBatch
from app.models.embedding_cache import EmbeddingCache
from app.models.evaluation import EvalRun
from app.models.feedback import Feedback
from app.models.rate_limit import RateLimit
from app.models.usage import LlmUsage
from app.models.user import User

__all__ = ["BatchStatus", "Chat", "DocStatus", "Document", "EmbeddingCache", "EvalRun", "Feedback", "IngestBatch", "LlmUsage", "Message", "RateLimit", "User"]
