"""Multimodal RAG, the practical way: turn images into text at index time (VISION_MODEL).

Documents are more than text: an org chart, a process diagram, a chart of last year's sales, a scanned
table. Text extraction skips them, so their content is invisible to search. Two ways to fix that:

    1. caption at index time (here)   a vision model describes each image ("Org chart: Meera Rao heads
                                      Engineering; reporting to her: ...") and the description is indexed
                                      with the page's text. Works with the existing text pipeline,
                                      embeddings and LLM; costs one vision call per image, once.
    2. multimodal embeddings          embed the images themselves (CLIP, ColPali) and give a vision LLM the
                                      retrieved images at answer time. Keeps detail a caption drops, but
                                      needs a vision model on every question and a second index.

Most production systems start with 1. The caption prompt asks for the facts (names, numbers, labels),
not a description of the style: "a blue bar chart" helps no one, "sales grew from 4.1M to 5.3M" does.
"""

import base64
import logging

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

from app.core.config import OLLAMA_URL, VISION_MODEL

log = logging.getLogger(__name__)

PROMPT = (
    "This image is from a company document. Write down the information it contains so it can be "
    "found by search: every visible text, names, numbers, labels, and what the chart, diagram or table "
    "shows (trends, relationships, steps). Plain sentences, no styling details. If it is a logo or "
    "decoration with no information, answer only: decoration"
)
MIN_BYTES = 5_000  # icons, bullets and lines aren't worth a model call
MAX_PER_PAGE = 4

_model = ChatOllama(model=VISION_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=400) if VISION_MODEL else None


def is_enabled() -> bool:
    return _model is not None


def _mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return "image/jpeg"


def describe(data: bytes) -> str | None:
    """What the image says, as text; None for decoration, tiny images, or when the model fails."""
    if _model is None or len(data) < MIN_BYTES:
        return None
    url = f"data:{_mime(data)};base64,{base64.b64encode(data).decode()}"
    try:
        out = _model.invoke([HumanMessage(content=[
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": url},
        ])])
    except Exception:
        log.exception("vision model failed on an image; indexing without its description")
        return None
    text = (out.content or "").strip()
    return None if not text or text.lower().startswith("decoration") else text
