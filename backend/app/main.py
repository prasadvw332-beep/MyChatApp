from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, documents, evals
from app.config import CORS_ORIGINS

app = FastAPI(title="MyChatApp API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sources"],
)

app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(evals.router)
