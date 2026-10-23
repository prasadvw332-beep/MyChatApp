"""Custom metrics for small judge models.

RAGAS' ContextRecall asks the judge for one JSON list classifying every reference statement.
A 1B model can't end that list: it keeps adding context chunks as "statements" until it runs
out of tokens. This version measures the same thing (share of reference sentences that the
retrieved chunks support) with one constrained yes/no question per sentence, which small
models handle reliably.
"""

import re
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

# Ollama constrains decoding to this schema, so the reply is always {"verdict": "yes"|"no"}
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["verdict"],
}

SUPPORT_SYSTEM = (
    "You check whether a statement is supported by a context. Answer \"yes\" if the context "
    "contains the information in the statement, otherwise \"no\". Judge only by the context."
)


def split_sentences(text: str) -> list[str]:
    # split on sentence ends and semicolons: smaller claims are easier for a small model to verify
    return [s.strip(" ;") for s in re.split(r"(?<=[.!?])\s+|;\s*", text) if len(s.strip(" ;")) > 3]


class SimpleContextRecall:
    def __init__(self, model: str, base_url: str):
        self.llm = ChatOllama(
            model=model, base_url=base_url, temperature=0, num_predict=16, format=VERDICT_SCHEMA
        )

    async def supported(self, statement: str, context: str) -> bool:
        out = await self.llm.ainvoke([
            SystemMessage(SUPPORT_SYSTEM),
            HumanMessage(f"Context:\n{context}\n\nStatement: {statement}\n\nIs the statement supported by the context?"),
        ])
        return '"yes"' in out.content.lower()

    async def ascore(self, user_input: str, retrieved_contexts: list[str], reference: str):
        statements = split_sentences(reference)
        if not statements:
            raise ValueError("Reference answer has no sentences to check.")
        context = "\n\n".join(retrieved_contexts)
        hits = [await self.supported(s, context) for s in statements]
        return SimpleNamespace(value=sum(hits) / len(hits))  # same shape as a RAGAS MetricResult
