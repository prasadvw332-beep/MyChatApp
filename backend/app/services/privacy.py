"""Personal data: export everything about a user, delete everything about a user (GDPR / DPDP).

People have the right to get a copy of their data (Art. 15/20 GDPR) and to have it erased (Art. 17).
In a RAG app "their data" is spread wider than the users table:

    PostgreSQL   account, chats and messages, feedback, agent memories          -> deleted with the user row
                 (foreign keys ON DELETE CASCADE), usage rows and prompt runs   -> kept, user id set to NULL
                 (aggregate cost stats stay correct but can't be linked back)
    documents    files only they could see ("user:<id>" alone)                  -> deleted: file, chunks, row
                 shared files that list them                                     -> their entry removed
    caches       semantic answer cache                                           -> cleared
                 page text and graph triples of the deleted files (they're       -> removed
                 keyed by content, so deleting the document leaves them)
    elsewhere    logs (Loki keeps 7 days, then they expire), traces (Tempo, Langfuse: delete by user id
                 through their APIs), backups (expire on their schedule; never restore deleted users)

The audit log keeps an "account deleted" event without personal details: proof the request was
honoured is itself something you must be able to show.
"""

from sqlalchemy import select

from app.cache import semantic
from app.clients import storage
from app.core.db import SessionLocal
from app.models import Chat, Document, Feedback, User, UserMemory
from app.rag import knowledge_base


def export(user: User) -> dict:
    with SessionLocal() as db:
        chats = db.scalars(select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at)).all()
        feedback = db.scalars(select(Feedback).where(Feedback.user_id == user.id)).all()
        memories = db.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
        owned = db.scalars(select(Document).where(Document.acl.contains([f"user:{user.id}"]))).all()
        return {
            "account": {"id": user.id, "username": user.username, "groups": user.groups or [],
                        "created_at": user.created_at.isoformat(), "sso": bool(user.oidc_subject)},
            "chats": [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat(),
                       "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                                    for m in c.messages]} for c in chats],
            "feedback": [{"chat_id": f.chat_id, "rating": f.rating, "comment": f.comment, "question": f.question,
                          "created_at": f.created_at.isoformat()} for f in feedback],
            "memories": [{"text": m.text, "created_at": m.created_at.isoformat()} for m in memories],
            "documents": [{"doc_id": d.doc_id, "filename": d.filename, "acl": d.acl} for d in owned],
        }


def erase(user: User) -> dict:
    """Delete the user and their personal data. Returns counts, for the audit log."""
    me = f"user:{user.id}"
    deleted_docs = unshared = 0
    with SessionLocal() as db:
        docs = db.scalars(select(Document).where(Document.acl.contains([me]))).all()
        plan = [(d.doc_id, d.filename, d.minio_key, [p for p in (d.acl or []) if p != me]) for d in docs]
    for doc_id, filename, minio_key, others in plan:
        if others:  # shared with a group or another user: they keep it, without us
            knowledge_base.set_acl(doc_id, others)
            unshared += 1
        else:
            if minio_key:  # before the file goes: the cache keys are computed from it
                knowledge_base.forget_cached(filename, storage.read(minio_key)[0])
            knowledge_base.delete_document(doc_id)
            deleted_docs += 1
    with SessionLocal.begin() as db:
        row = db.get(User, user.id)
        if row:
            db.delete(row)  # chats, messages, feedback, memories: ON DELETE CASCADE
    semantic.forget_all()
    return {"documents_deleted": deleted_docs, "documents_unshared": unshared}
