import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import config, errors
from app.core.logging import setup_logging
from app.core.middleware import RequestContextMiddleware

setup_logging()
config.validate()  # before anything connects: a misconfigured production pod should crash, loudly

from app.agents import tools as agent_tools  # noqa: E402  (after logging/config, so imports log correctly)
from app.api.router import api_router  # noqa: E402
from app.core.db import init_db  # noqa: E402
from app.rag import knowledge_base  # noqa: E402

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        added = await asyncio.to_thread(knowledge_base.sync_documents)
        if added:
            log.info("Recorded metadata for %d existing document(s)", added)
    except Exception:
        if config.IS_PRODUCTION:
            raise  # Kubernetes restarts the pod with backoff until PostgreSQL is reachable
        log.exception("Startup tasks failed (is PostgreSQL running?); /health/ready will report it")
    # MCP sessions for /agent/chat, open for the app's lifetime
    async with agent_tools.connect() as tools:
        app.state.agent_tools = tools
        log.info("MyChatApp API started (env=%s, agent tools: %s)", config.APP_ENV, ", ".join(t.name for t in tools))
        yield


app = FastAPI(title="MyChatApp API", lifespan=lifespan)
errors.register(app)
# inside CORS (added first = inner), so even a crash's 500 carries CORS headers the browser can read
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    # dev only: Vite moves to 5174+ when 5173 is busy. Never in production: any local page could use your session.
    allow_origin_regex=None if config.IS_PRODUCTION else r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sources", "X-Request-ID"],
)

app.include_router(api_router)
