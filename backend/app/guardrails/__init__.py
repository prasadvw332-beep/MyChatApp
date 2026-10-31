"""Guardrails: what the model is allowed to read, and what users are allowed to see (docs/guardrails.md).

    injection.py  prompt injection: in the user's message, in document chunks, in agent tool results;
                  fences documents in the prompt; a canary marker to catch a leaked system prompt
    content.py    content safety with Llama Guard (GUARD_MODEL) on the question and the finished answer;
                  AnswerGuard watches a streamed answer
    events.py     every finding is counted (Prometheus), audited, and acted on only in enforce mode

GUARDRAILS_MODE: enforce (default) | monitor (count and audit only) | off.
"""

from app.guardrails import content, injection


async def check_question(question: str) -> None:
    """Raise Blocked if the user's newest message is an injection attempt or asks for blocked content."""
    injection.check_question(question)  # instant, so it runs before the model call
    await content.check_question(question)
