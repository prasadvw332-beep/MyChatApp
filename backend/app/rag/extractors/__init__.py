"""Text extraction: file bytes -> [(page number or None, text)], for chunking.py.

Which extractor runs is set by EXTRACTOR:

    basic         pypdf + python-docx (default, light): PDFs with a text layer, DOCX
    docling       Docling, local: layout-aware PDFs (reading order, tables), OCR for scans, plus
                  PPTX, XLSX, HTML and images. Heavy (PyTorch models):  uv add docling
    unstructured  Unstructured, local: the same breadth, element-based (Title, NarrativeText,
                  Table, ...):  uv add "unstructured[all-docs]"  (+ Tesseract and Poppler for OCR)
    azure         Azure AI Document Intelligence, a cloud API (prebuilt-layout): OCR and layout
                  without local models; AZURE_DI_ENDPOINT and AZURE_DI_KEY

Every extractor returns Markdown-ish text, headings as '#' lines, so chunking splits sections the
same way whichever one ran. TXT and MD are always read as they are. The three optional ones are
imported only when chosen, so the default install doesn't need their (large) packages.

PDFs go through the page cache when extraction is slow or paid (app/cache/extraction.py): an edited
document only re-extracts the pages that changed.
"""

import importlib
import importlib.metadata
import os
from functools import cache

from app.cache.extraction import PageCache
from app.core.config import EXTRACT_CACHE, EXTRACT_TEXT_LAYER_MIN_CHARS, EXTRACTOR, VISION_MODEL
from app.core.errors import InvalidInputError
from app.repositories import extraction_cache_store

Page = tuple[int | None, str]  # (page number, text); None when the format has no fixed pages
# Part of every cached page's key. Bump it when an extractor's own code here changes its output (a new
# heading rule, table format, figure caption), so pages cached before the change are extracted again.
CACHE_VERSION = 1

EXTRACTORS = {"basic": "basic", "docling": "docling_ext", "unstructured": "unstructured_ext", "azure": "azure_di"}
if EXTRACTOR not in EXTRACTORS:
    raise ValueError(f"EXTRACTOR={EXTRACTOR!r}: use one of {', '.join(EXTRACTORS)}")

BASIC_TYPES = {".pdf", ".docx", ".txt", ".md"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tiff"}
RICH_TYPES = {".pptx", ".xlsx", ".html", *IMAGE_TYPES}  # docling, unstructured, azure
# basic + a vision model (VISION_MODEL) reads images too: described by the model (app/rag/vision.py)
SUPPORTED = BASIC_TYPES | (RICH_TYPES if EXTRACTOR != "basic" else IMAGE_TYPES if VISION_MODEL else set())


class UnreadableDocumentError(InvalidInputError, ValueError):
    """The file has no extractable text. Permanent: retrying won't help."""

    code = "unreadable_document"


def extract(filename: str, data: bytes) -> list[Page]:
    if os.path.splitext(filename)[1].lower() in (".txt", ".md"):
        return [(None, data.decode("utf-8", errors="replace"))]
    module = importlib.import_module(f"app.rag.extractors.{EXTRACTORS[EXTRACTOR]}")
    pages = page_cache()
    if pages and os.path.splitext(filename)[1].lower() == ".pdf":
        return pages.extract(filename, data, module.extract)
    return module.extract(filename, data)


def forget(filename: str, data: bytes) -> None:
    """Remove a file's cached pages (personal data erasure)."""
    pages = page_cache()
    if pages and os.path.splitext(filename)[1].lower() == ".pdf":
        pages.forget(data)


@cache
def page_cache() -> PageCache | None:
    """None where extraction is cheap: pypdf reads a text layer faster than PostgreSQL returns it."""
    expensive = EXTRACTOR != "basic" or bool(VISION_MODEL)
    rows = extraction_cache_store.pages if EXTRACT_CACHE and expensive else None
    # basic already reads the text layer; routing it there would only drop the VISION_MODEL figure descriptions
    min_chars = EXTRACT_TEXT_LAYER_MIN_CHARS if EXTRACTOR != "basic" else 0
    if rows is None and min_chars <= 0:
        return None
    return PageCache(_extractor_id(), rows, min_chars)


def _extractor_id() -> str:
    """What produces a page's text, for the cache key: another extractor, package version, vision model or
    CACHE_VERSION gives other keys, so text written before the change is never reused."""
    if EXTRACTOR == "azure":
        from app.rag.extractors.azure_di import API_VERSION as version
    else:
        package = {"basic": "pypdf", "docling": "docling", "unstructured": "unstructured"}[EXTRACTOR]
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed"  # extracting fails anyway, and says what to install
    vision = VISION_MODEL if EXTRACTOR == "basic" else ""
    return f"{EXTRACTOR}:{version}:{vision}:v{CACHE_VERSION}"
