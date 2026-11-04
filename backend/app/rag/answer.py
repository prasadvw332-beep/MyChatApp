"""Turn retrieved chunks + chat history into model messages. Shared by the API and the evals."""

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.guardrails import pii
from app.guardrails.injection import fence, with_canary
from app.prompts.rag import PLAIN_PROMPT, RAG_PROMPT
from app.schemas.chat import ChatMessage


def format_context(docs: list[Document], facts: list[str] | None = None) -> str:
    context = "\n\n".join(fence(d.page_content, d.metadata.get("source"), d.metadata.get("page")) for d in docs)
    if facts:  # from the knowledge graph (app/rag/graph.py): relations that connect the documents
        context += "\n\n" + fence("\n".join(facts), "knowledge graph", None)
    return context


def build_messages(history: list[ChatMessage], docs: list[Document], facts: list[str] | None = None,
                   rag_prompt: str = RAG_PROMPT) -> list[BaseMessage]:
    redact = pii.redact if pii.before_llm() else (lambda text: text)  # PII_REDACTION=llm|both
    context = redact(format_context(docs, facts)) if docs or facts else ""
    system = rag_prompt.format(context=context) if context else PLAIN_PROMPT
    messages: list[BaseMessage] = [SystemMessage(with_canary(system))]
    for m in history:
        messages.append(AIMessage(redact(m.content)) if m.role == "assistant" else HumanMessage(redact(m.content)))
    return messages


def cite(docs: list[Document]) -> list[dict]:
    """One source entry per (file, page), in retrieval order."""
    seen, sources = set(), []
    for d in docs:
        key = (d.metadata.get("source"), d.metadata.get("page"))
        if key not in seen:
            seen.add(key)
            sources.append({"source": key[0], "page": key[1], "doc_id": d.metadata.get("doc_id")})
    return sources
