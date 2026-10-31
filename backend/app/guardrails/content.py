"""Content safety: Llama Guard checks the question and the finished answer; AnswerGuard watches the stream.

Llama Guard 3 (GUARD_MODEL, e.g. llama-guard3:1b on Ollama) is a classifier: given a conversation it
answers "safe" or "unsafe" plus hazard categories (S1 violent crimes ... S14). Only the categories in
GUARD_BLOCK_CATEGORIES are blocked.

It fails open: if the guard model is down, the request goes ahead, with a warning and an `error`
metric. A chat that stops working whenever the classifier hiccups is worse here than one unchecked
answer; set an alert on guardrail_events_total{action="error"} instead.

An answer streams to the browser as it is generated, so a problem can only be found after some of it
is on screen. Withholding sends a marker the frontend acts on by replacing the whole answer with the
notice (and not saving or resending the original):
  /chat         WITHHELD ("\\x00") then the notice, in the text stream
  /agent/chat   a {"type": "withheld", "detail": notice} event
"""

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage

from app.clients import llm
from app.core import metrics
from app.core.config import GUARD_BLOCK_CATEGORIES, GUARD_MODEL
from app.core.errors import Blocked
from app.guardrails import events
from app.guardrails.injection import CANARY
from app.services import usage

log = logging.getLogger(__name__)

WITHHELD = "\x00"

# Llama Guard 3's hazard taxonomy
CATEGORIES = {
    "S1": "violent crimes", "S2": "non-violent crimes", "S3": "sex-related crimes", "S4": "child sexual exploitation",
    "S5": "defamation", "S6": "specialized advice", "S7": "privacy", "S8": "intellectual property",
    "S9": "indiscriminate weapons", "S10": "hate", "S11": "suicide and self-harm", "S12": "sexual content",
    "S13": "elections", "S14": "code interpreter abuse",
}
UNSPECIFIED = "unsafe"  # the verdict named no category: blocked, to be safe

CONTENT_MESSAGE = "This message can't be answered: it asks for content this assistant doesn't provide."
SELF_HARM_NOTE = (" If you're going through a difficult time, please consider talking to someone you trust "
                  "or a local crisis line.")
WITHHELD_CONTENT = "This answer was withheld: it contained content this assistant doesn't provide."
WITHHELD_LEAK = "This answer was withheld: it revealed the assistant's internal instructions."


async def classify(question: str, answer: str | None = None) -> set[str]:
    """Llama Guard's unsafe categories for the question (or for the answer to it). Empty when it judges
    the text safe, when no GUARD_MODEL is set, and when the check itself fails."""
    if llm.guard_llm is None:
        return set()
    check = "content_output" if answer is not None else "content_input"
    messages = [HumanMessage(question)] + ([AIMessage(answer)] if answer is not None else [])
    try:
        out = await llm.guard_llm.ainvoke(messages)
    except Exception:
        log.warning("guard model %s failed; %s not checked", GUARD_MODEL, check, exc_info=True)
        metrics.guardrail_events.labels(check, "error").inc()
        return set()
    tally = usage.Tally()
    tally.add(out)
    await usage.record("guard", tally, GUARD_MODEL)
    return parse_verdict(out.content or "")


def parse_verdict(text: str) -> set[str]:
    """ "unsafe\\nS1,S10" -> {"S1", "S10"}; "safe" -> set()"""
    first, _, rest = text.strip().partition("\n")
    if first.strip().lower() != "unsafe":
        return set()
    return set(re.findall(r"S\d+", rest.upper())) or {UNSPECIFIED}


def blocked(categories: set[str]) -> set[str]:
    return {c for c in categories if c in GUARD_BLOCK_CATEGORIES or c == UNSPECIFIED}


async def check_question(question: str) -> None:
    if not events.ACTIVE:
        return
    found = blocked(await classify(question))
    if found and events.trip("content_input", categories=",".join(sorted(found))):
        raise Blocked(CONTENT_MESSAGE + (SELF_HARM_NOTE if "S11" in found else ""))


class AnswerGuard:
    """Follows one streamed answer: check() each piece as it arrives, finish() once it is complete.
    Both return a notice when the answer must be withheld, else None."""

    def __init__(self, question: str):
        self.question = question
        self.text = ""
        self._leak_seen = False

    def check(self, piece: str) -> str | None:
        self.text += piece
        if not events.ACTIVE or self._leak_seen:
            return None
        # the marker may arrive split over several pieces: look at the end of the text, not just the piece
        if CANARY in self.text[-(len(piece) + len(CANARY)):]:
            self._leak_seen = True  # counted once per answer, even in monitor mode
            if events.trip("prompt_leak"):
                return WITHHELD_LEAK
        return None

    async def finish(self) -> str | None:
        if not events.ACTIVE or not self.text.strip():
            return None
        found = blocked(await classify(self.question, self.text))
        if found and events.trip("content_output", categories=",".join(sorted(found))):
            return WITHHELD_CONTENT
        return None
