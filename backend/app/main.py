import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, chats, documents, evals
from app.config import CORS_ORIGINS
from app.db import init_db
from app.services import rag



@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        init_db()
        added = await asyncio.to_thread(rag.sync_documents)
        if added:
            logging.getLogger("uvicorn.error").info("Recorded metadata for %d existing document(s)", added)
    except Exception as e:  # chat and documents don't need Postgres; only /evals does
        logging.getLogger("uvicorn.error").warning("PostgreSQL unavailable, eval results won't work: %s", e)
    yield


app = FastAPI(title="MyChatApp API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sources"],
)

app.include_router(chat.router)
app.include_router(chats.router)
app.include_router(documents.router)
app.include_router(evals.router)
