"""All HTTP routes in one router, mounted by main.py."""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.routes import agent, auth, chat, chats, documents, evals, feedback, health, ingest

api_router = APIRouter()

# public
api_router.include_router(health.router)
api_router.include_router(auth.router)

# everything else needs a login (chats are additionally scoped to the user, see routes/chats.py)
protected = [CurrentUser]
api_router.include_router(chat.router, dependencies=protected)
api_router.include_router(agent.router, dependencies=protected)
api_router.include_router(chats.router, dependencies=protected)
api_router.include_router(documents.router, dependencies=protected)
api_router.include_router(evals.router, dependencies=protected)
api_router.include_router(feedback.router, dependencies=protected)
api_router.include_router(ingest.router, dependencies=protected)
