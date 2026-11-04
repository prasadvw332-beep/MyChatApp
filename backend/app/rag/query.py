"""Query rewriting: turn what the user typed into better search queries, before retrieval.

    QUERY_REWRITE=condense  "what about Pune?" after a question on leave policy
                            -> "What is the leave policy for the Pune office?"
                            Follow-ups are unsearchable on their own; the chat history fills them in.
    QUERY_REWRITE=multi     the (condensed) question plus QUERY_VARIANTS rephrasings, each searched,
                            results fused with RRF: one unlucky wording no longer decides the answer.
    QUERY_REWRITE=hyde      Hypothetical Document Embeddings: the model writes a short fake answer,
                            and that is searched. An answer looks more like the chunk that holds the
                            real answer than a question does. Its facts may be wrong: it's only used
                            to search, never shown.

Each costs one extra model call before the search starts, so time to first token goes up. The
rewrite only feeds the search: the answer is still written to the user's own words.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.clients.llm import utility_llm
from app.core.config import QUERY_REWRITE, QUERY_VARIANTS
from app.core.telemetry import span
from app.schemas.chat import ChatMessage

log = logging.getLogger(__name__)

CONDENSE = (
    "Rewrite the user's last message as one standalone search query, using the conversation for "
    "missing context (names, topics, what 'it' or 'that' refers to). Reply with the query only."
)
VARIANTS = (
    "Write {n} different search queries that would find documents answering the question. Use other "
    "words and synonyms; keep names, codes and numbers as they are. One query per line, nothing else."
)
HYDE = (
    "Write a short passage (3-4 sentences) that could appear in a company document answering the "
    "question. If you don't know, invent plausible details. Reply with the passage only."
)


async def search_queries(history: list[ChatMessage]) -> list[str]:
    """What to search for. Never fails: on any model error, the user's own last message."""
    question = next((m.content for m in reversed(history) if m.role == "user"), "")
    if QUERY_REWRITE == "off" or not question.strip():
        return [question]
    with span("rag.rewrite", mode=QUERY_REWRITE):
        try:
            standalone = await _condense(history) if len(history) > 1 else question
            if QUERY_REWRITE == "multi":
                return [standalone, *await _variants(standalone)]
            if QUERY_REWRITE == "hyde":
                return [standalone, await _ask(HYDE, standalone)]
            return [standalone]
        except Exception:
            log.exception("query rewriting failed; searching the question as typed")
            return [question]


async def _ask(system: str, text: str) -> str:
    out = await utility_llm.ainvoke([SystemMessage(system), HumanMessage(text)])
    return (out.content or "").strip()


async def _condense(history: list[ChatMessage]) -> str:
    convo = "\n".join(f"{m.role}: {m.content[:500]}" for m in history[-6:])  # the recent turns are enough
    return await _ask(CONDENSE, convo) or history[-1].content


async def _variants(question: str) -> list[str]:
    lines = (await _ask(VARIANTS.format(n=QUERY_VARIANTS), question)).splitlines()
    return [line.strip(" -*0123456789.").strip() for line in lines if line.strip()][:QUERY_VARIANTS]
