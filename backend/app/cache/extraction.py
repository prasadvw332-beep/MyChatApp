"""Extraction cache: each PDF page's text is extracted once, so an edited document only re-extracts the
pages that changed.

OCR and layout extraction (docling, unstructured, azure) and image descriptions (VISION_MODEL) cost
seconds or money per page. Without this, one changed page of a 300-page manual re-extracts all 300.

    split     pypdf reads the page tree: cheap, no OCR and no rendering
    key       sha256(extractor id + the page's content). The content is hashed from the page's objects
              (content streams, fonts, images, annotations), not from the file's bytes: saving a file
              again renumbers and moves its objects, which would give every page a new key
    look up   PostgreSQL (extraction_cache), every page in one query
    route     optional (EXTRACT_TEXT_LAYER_MIN_CHARS): a missing page that already has a text layer is
              read with pypdf instead, so only scanned pages go to OCR. Not stored: re-reading it is free
    extract   only the remaining pages, copied into a smaller PDF and given to the extractor as usual
    store     their text; the whole document's pages are returned in order

Trade-offs:
- Only PDFs. DOCX, PPTX, XLSX and HTML have no fixed pages to key, so they are extracted whole.
- After an edit the changed pages are extracted without the rest of the document. Every extractor
  here already returns text page by page, but one that looks across pages (Unstructured choosing
  "fast" or "hi_res" for the whole file) can decide differently for a few pages than for the whole.
- Like the embedding cache, it never fails an extraction: PostgreSQL down -> every page is extracted.
"""

import hashlib
import logging
from collections.abc import Callable
from io import BytesIO
from typing import Protocol

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from app.core import metrics

log = logging.getLogger(__name__)

Page = tuple[int | None, str]  # as app/rag/extractors returns them
Extract = Callable[[str, bytes], list[Page]]

# Entries that point back up the page tree or into the document's structure: they change when other pages
# do, and say nothing about this page's text. Inherited ones (/Resources, /MediaBox, ...) are not lost by
# skipping /Parent: pypdf copies them onto each page when it reads the page tree.
_NOT_CONTENT = frozenset({"/Parent", "/P", "/StructParents", "/StructParent"})
# how a stream is compressed; its decoded data is what's hashed, so re-compressing a file changes no key
_ENCODING = frozenset({"/Length", "/Filter", "/DecodeParms"})
_CYCLE = hashlib.sha256(b"cycle").digest()  # an object met again while hashing itself: already being counted
_PAGE_LINK = hashlib.sha256(b"page").digest()  # a link to a page (a /Dest): that page's content is its own business


class TextRows(Protocol):
    """The persistent tier: repositories/extraction_cache_store.pages, or a dict in tests."""

    def get_many(self, keys: list[str]) -> dict[str, str]: ...

    def put_many(self, extractor: str, texts: dict[str, str]) -> None: ...

    def delete_many(self, keys: list[str]) -> None: ...


class PageCache:
    def __init__(self, extractor: str, rows: TextRows | None, text_layer_min_chars: int = 0):
        self.extractor = extractor  # what produces the text: another one must never reuse it
        self.rows = rows  # None: no caching, only routing text-layer pages
        self.text_layer_min_chars = text_layer_min_chars

    def key(self, page_digest: str) -> str:
        return hashlib.sha256(f"{self.extractor}\0{page_digest}".encode()).hexdigest()

    def extract(self, filename: str, data: bytes, extract: Extract) -> list[Page]:
        try:
            reader = PdfReader(BytesIO(data))
            keys = [self.key(d) for d in page_digests(reader)]
        except Exception as e:  # encrypted or damaged: the extractor reads it whole, or says why it can't
            log.warning("could not split %s into pages, extracting it whole: %s", filename, e)
            return extract(filename, data)

        stored = self._load(keys)
        texts = {i: stored[k] for i, k in enumerate(keys) if k in stored}  # page index -> text
        metrics.extraction_cache.labels("database").inc(len(texts))
        if self.text_layer_min_chars > 0:
            routed = self._text_layer(reader, [i for i in range(len(keys)) if i not in texts])
            metrics.extraction_cache.labels("text_layer").inc(len(routed))
            texts |= routed
        missing = [i for i in range(len(keys)) if i not in texts]
        if not missing:
            return [(i + 1, texts[i]) for i in range(len(keys))]

        metrics.extraction_cache.labels("miss").inc(len(missing))
        part = data if len(missing) == len(keys) else _subset(reader, missing)
        pages = extract(filename, part)
        fresh = _by_position(pages, missing)
        if fresh is None:  # unnumbered pages: there's no telling which text belongs to which page
            log.warning("%s: the extractor did not number its pages, extracting it whole without the cache", filename)
            return pages if part is data else extract(filename, data)
        self._save({keys[i]: text for i, text in fresh.items()})
        texts |= fresh
        return [(i + 1, texts[i]) for i in range(len(keys))]

    def forget(self, data: bytes) -> None:
        """Remove a PDF's pages from the cache (personal data erasure). Other documents sharing a page only
        lose their cached copy of it."""
        if not self.rows:
            return
        try:
            keys = [self.key(d) for d in page_digests(PdfReader(BytesIO(data)))]
        except Exception as e:  # not a PDF we could split: nothing of it was cached
            log.info("no cached pages to forget: %s", e)
            return
        self.rows.delete_many(keys)  # raises: an erasure must not report success when it failed

    def _text_layer(self, reader: PdfReader, indexes: list[int]) -> dict[int, str]:
        routed = {}
        for i in indexes:
            try:
                text = reader.pages[i].extract_text() or ""
            except Exception:  # pypdf can't read this page's text: the extractor gets it
                continue
            if len(text.strip()) >= self.text_layer_min_chars:
                routed[i] = text
        return routed

    def _load(self, keys: list[str]) -> dict[str, str]:
        if not self.rows:
            return {}
        try:
            return self.rows.get_many(keys)
        except Exception as e:
            metrics.cache_errors.labels("extraction").inc()
            log.warning("extraction cache read failed, extracting every page: %s", e)
            return {}

    def _save(self, texts: dict[str, str]) -> None:
        if not self.rows or not texts:
            return
        try:
            self.rows.put_many(self.extractor, texts)
        except Exception as e:
            metrics.cache_errors.labels("extraction").inc()
            log.warning("extraction cache write failed: %s", e)


def page_digests(reader: PdfReader) -> list[str]:
    """A digest of each page's content, the same however the file was saved."""
    memo: dict[tuple[int, int], bytes] = {}  # objects shared by pages (fonts, logos) are hashed once
    return [_digest(page, memo, root=True).hex() for page in reader.pages]


def _digest(obj, memo: dict[tuple[int, int], bytes], root: bool = False) -> bytes:
    if isinstance(obj, IndirectObject):
        ref = (obj.idnum, obj.generation)
        if ref not in memo:
            memo[ref] = _CYCLE
            memo[ref] = _digest(obj.get_object(), memo)
        return memo[ref]
    h = hashlib.sha256(_kind(obj))
    if isinstance(obj, DictionaryObject):
        if not root and obj.get("/Type") == "/Page":
            return _PAGE_LINK
        skip = _NOT_CONTENT
        if isinstance(obj, StreamObject):
            try:
                data = obj.get_data()
                skip = _NOT_CONTENT | _ENCODING
            except Exception:  # a filter pypdf can't decode: the encoded bytes, with the filter that made them
                data = obj._data
            h.update(len(data).to_bytes(8, "big") + data)  # the length: data and the digests after it can't blur
        for name in sorted(obj):
            if name not in skip:
                h.update(_digest(name, memo))
                h.update(_digest(obj.raw_get(name), memo))  # raw: an IndirectObject, so it's memoised
    elif isinstance(obj, ArrayObject):
        for item in obj:
            h.update(_digest(item, memo))
    elif isinstance(obj, int | float) and not isinstance(obj, bool):  # 612 and 612.0 are the same width
        h.update(str(int(obj) if float(obj).is_integer() else float(obj)).encode())
    else:  # name, string, boolean, null
        h.update(bytes(obj) if isinstance(obj, bytes) else str(obj).encode("utf-8", "surrogatepass"))
    return h.digest()


def _kind(obj) -> bytes:
    """What an object is, not which pypdf class holds it: a stream is a Decoded- or EncodedStreamObject
    depending on whether it was compressed, a number a NumberObject or FloatObject depending on the writer."""
    if isinstance(obj, StreamObject):
        return b"stream"
    if isinstance(obj, DictionaryObject):
        return b"dict"
    if isinstance(obj, ArrayObject):
        return b"array"
    if isinstance(obj, int | float) and not isinstance(obj, bool):
        return b"number"
    return type(obj).__name__.encode()


def _subset(reader: PdfReader, indexes: list[int]) -> bytes:
    writer = PdfWriter()
    for i in indexes:
        writer.add_page(reader.pages[i])
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _by_position(pages: list[Page], indexes: list[int]) -> dict[int, str] | None:
    """The extractor's pages of the smaller PDF (numbered 1..n) -> {original page index: text}.
    A page it left out had nothing to read: ''. None if the pages aren't numbered within 1..n."""
    numbered: dict[int, str] = {}
    for number, text in pages:
        if number is None or not 1 <= number <= len(indexes):
            return None
        numbered[number] = numbered.get(number, "") + text
    return {i: numbered.get(n, "") for n, i in enumerate(indexes, start=1)}
