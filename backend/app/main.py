import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import CORS_ORIGINS
from app.core.db import init_db
from app.rag import knowledge_base


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        init_db()
        added = await asyncio.to_thread(knowledge_base.sync_documents)
        if added:
            logging.getLogger("uvicorn.error").info("Recorded metadata for %d existing document(s)", added)
    except Exception as e:  # chat and documents don't need Postgres; only /evals does
        logging.getLogger("uvicorn.error").warning("PostgreSQL unavailable, eval results won't work: %s", e)
    yield


app = FastAPI(title="MyChatApp API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",  # Vite moves to 5174+ when 5173 is busy
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sources"],
)

app.include_router(api_router)
