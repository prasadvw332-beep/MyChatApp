"""Where agent runs keep their state between steps: the LangGraph checkpointer.

After every node (agent, approve, tools) LangGraph saves the graph state under the run's thread id.
That's what lets a run *stop and continue later*: when a tool needs the user's approval, the run
pauses (interrupt), the HTTP response ends, and a second request (POST /agent/resume) picks it up
from the saved state, possibly on another backend replica.

    AGENT_CHECKPOINTER=memory     in this process's memory (default): fine for one replica and for
                                  learning; a resume must reach the same process, and a restart
                                  forgets paused runs
    AGENT_CHECKPOINTER=postgres   in PostgreSQL: any replica can resume, survives restarts.
                                  uv add langgraph-checkpoint-postgres psycopg-pool

The same mechanism gives "time travel" (inspect or replay a run from any step) and durable,
long-running agents. Long-term memory across chats is a different store: memory.py.
"""

import asyncio

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import AGENT_CHECKPOINTER, DATABASE_URL

memory = InMemorySaver()
_postgres: BaseCheckpointSaver | None = None
_lock = asyncio.Lock()


async def saver() -> BaseCheckpointSaver:
    global _postgres
    if AGENT_CHECKPOINTER != "postgres":
        return memory
    async with _lock:  # one pool for the process, created by the first request that needs it
        if _postgres is None:
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                from psycopg.rows import dict_row
                from psycopg_pool import AsyncConnectionPool
            except ImportError as e:
                raise RuntimeError("AGENT_CHECKPOINTER=postgres needs: uv add langgraph-checkpoint-postgres psycopg-pool") from e
            # psycopg wants a plain postgresql:// URL, not SQLAlchemy's postgresql+psycopg://
            pool = AsyncConnectionPool(DATABASE_URL.replace("+psycopg", ""), open=False, max_size=10,
                                       kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
            await pool.open()
            saver_ = AsyncPostgresSaver(pool)
            await saver_.setup()  # creates its tables on first use (idempotent)
            _postgres = saver_
    return _postgres


async def forget(cp: BaseCheckpointSaver, thread_id: str) -> None:
    """Drop a finished run's checkpoints: nothing will resume it, and memory would only grow."""
    try:
        await cp.adelete_thread(thread_id)
    except Exception:
        pass  # best effort: an old checkpoint costs space, nothing else
