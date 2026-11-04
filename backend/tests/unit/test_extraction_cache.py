"""Page cache for extraction (app/cache/extraction.py) and the graph-triple cache (app/rag/graph.py).
Real PDFs, built with pypdf; the cache tables are dicts and the 'OCR' extractor is a fake that counts pages."""

from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.cache.extraction import PageCache, page_digests
from app.rag import extractors, graph
from app.rag import knowledge_base as kb
from tests.fakes import FakeVectorRows


def make_pdf(texts: list[str], font: str = "Helvetica", inherited: bool = False) -> bytes:
    """One page per text, all sharing one font object. An empty text is a page with nothing to read (a scan).
    inherited: the font sits on the page tree's root and the pages inherit it, as many PDF writers do."""
    writer = PdfWriter()
    font_ref = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject(f"/{font}"),
    }))
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    for text in texts:
        page = writer.add_blank_page(612, 792)
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
        if inherited:
            del page["/Resources"]  # add_blank_page gives every page an empty one of its own
        else:
            page[NameObject("/Resources")] = resources
    if inherited:
        writer._root_object["/Pages"][NameObject("/Resources")] = resources
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


class FakeOCR:
    """Plays a slow extractor: numbers pages 1..n like the real ones and records how many pages it was given."""

    def __init__(self, numbered: bool = True):
        self.calls: list[int] = []
        self.numbered = numbered

    def __call__(self, filename: str, data: bytes):
        pages = PdfReader(BytesIO(data)).pages
        self.calls.append(len(pages))
        return [((i + 1) if self.numbered else None, f"ocr:{p.extract_text()}") for i, p in enumerate(pages)]


@pytest.fixture
def rows():
    return FakeVectorRows()


@pytest.fixture
def cache(rows):
    return PageCache("docling:2.0:v1", rows)


def test_an_unchanged_document_is_not_extracted_again(cache, metric):
    ocr, pdf = FakeOCR(), make_pdf(["one", "two", "three"])
    first = cache.extract("a.pdf", pdf, ocr)
    before = metric("extraction_cache_lookups_total", result="database")

    assert cache.extract("a.pdf", pdf, ocr) == first == [(1, "ocr:one"), (2, "ocr:two"), (3, "ocr:three")]
    assert ocr.calls == [3]  # the second time, nothing
    assert metric("extraction_cache_lookups_total", result="database") == before + 3


def test_only_the_edited_page_is_extracted(cache):
    ocr = FakeOCR()
    cache.extract("a.pdf", make_pdf(["one", "two", "three"]), ocr)

    pages = cache.extract("a.pdf", make_pdf(["one", "TWO, edited", "three"]), ocr)
    assert ocr.calls == [3, 1]  # one page, alone in a smaller PDF
    assert pages == [(1, "ocr:one"), (2, "ocr:TWO, edited"), (3, "ocr:three")]  # original numbers and order


def test_saving_the_file_again_keeps_every_page_key():
    original = PdfReader(BytesIO(make_pdf(["one", "two"])))
    writer = PdfWriter(clone_from=original)  # renumbers objects, rewrites the layout
    for page in writer.pages:
        page.compress_content_streams()  # and re-encodes the content: same text, other bytes
    resaved = BytesIO()
    writer.write(resaved)

    assert page_digests(PdfReader(resaved)) == page_digests(original)


@pytest.mark.parametrize("inherited", [False, True])
def test_a_changed_font_changes_the_key_of_every_page_using_it(inherited):
    """Same content streams, other font: the text a reader gets can differ, so the cached text must not be reused.
    Also when the font is inherited from the page tree rather than on the page."""
    helvetica = page_digests(PdfReader(BytesIO(make_pdf(["one", "two"], "Helvetica", inherited))))
    courier = page_digests(PdfReader(BytesIO(make_pdf(["one", "two"], "Courier", inherited))))
    assert helvetica[0] != courier[0] and helvetica[1] != courier[1]


def test_another_extractor_never_reuses_the_text(rows):
    ocr, pdf = FakeOCR(), make_pdf(["one"])
    PageCache("docling:2.0:v1", rows).extract("a.pdf", pdf, ocr)
    PageCache("azure:2024-11-30:v1", rows).extract("a.pdf", pdf, ocr)
    assert ocr.calls == [1, 1]


def test_with_the_database_down_every_page_is_extracted(metric):
    cache = PageCache("docling:2.0:v1", FakeVectorRows(down=True))
    ocr, before = FakeOCR(), metric("cache_errors_total", cache="extraction")

    assert cache.extract("a.pdf", make_pdf(["one", "two"]), ocr) == [(1, "ocr:one"), (2, "ocr:two")]
    assert ocr.calls == [2]
    assert metric("cache_errors_total", cache="extraction") == before + 2  # the read and the write


def test_a_file_pypdf_cannot_split_goes_to_the_extractor_whole(cache):
    seen = []
    pages = cache.extract("a.pdf", b"not a pdf", lambda name, data: seen.append(data) or [(None, "whatever")])
    assert seen == [b"not a pdf"] and pages == [(None, "whatever")]  # the extractor decides, as without the cache


def test_unnumbered_pages_are_not_cached_and_the_file_is_extracted_whole(cache, rows):
    ocr = FakeOCR(numbered=False)
    assert cache.extract("a.pdf", make_pdf(["one", "two"]), ocr) == [(None, "ocr:one"), (None, "ocr:two")]
    assert ocr.calls == [2] and rows.rows == {}  # no second call when the whole file was the one extracted


def test_text_layer_pages_skip_the_extractor(rows, metric):
    cache = PageCache("docling:2.0:v1", rows, text_layer_min_chars=10)
    ocr, before = FakeOCR(), metric("extraction_cache_lookups_total", result="text_layer")

    pages = cache.extract("a.pdf", make_pdf(["a page with a text layer", ""]), ocr)
    assert ocr.calls == [1]  # only the page without text
    assert pages == [(1, "a page with a text layer"), (2, "ocr:")]
    assert metric("extraction_cache_lookups_total", result="text_layer") == before + 1
    assert len(rows.rows) == 1  # pypdf's text isn't stored: reading it again costs nothing


def test_forget_removes_a_documents_pages(cache, rows):
    pdf = make_pdf(["one", "two"])
    cache.extract("a.pdf", pdf, FakeOCR())
    cache.forget(pdf)
    assert rows.rows == {}


# ---------------------------------------------------------------- wiring in app/rag/extractors


@pytest.fixture
def fresh_page_cache():
    extractors.page_cache.cache_clear()
    yield
    extractors.page_cache.cache_clear()


def test_basic_without_a_vision_model_uses_no_cache(fresh_page_cache, monkeypatch):
    monkeypatch.setattr(extractors, "EXTRACTOR", "basic")
    monkeypatch.setattr(extractors, "VISION_MODEL", "")
    assert extractors.page_cache() is None  # pypdf reads a page faster than PostgreSQL returns it


def test_slow_extractors_are_cached_under_their_version(fresh_page_cache, monkeypatch):
    monkeypatch.setattr(extractors, "EXTRACTOR", "azure")
    cache = extractors.page_cache()
    assert cache is not None and cache.rows is not None
    assert cache.extractor == f"azure:2024-11-30::v{extractors.CACHE_VERSION}"


def test_cached_pages_read_the_same_as_extracting_directly(monkeypatch):
    """Through extract(), with the real basic extractor: the cache must not change what chunking sees."""
    pdf = make_pdf(["one", "two", "three"])
    direct = extractors.extract("a.pdf", pdf)
    monkeypatch.setattr(extractors, "page_cache", lambda: PageCache("basic:test:v1", FakeVectorRows()))
    assert extractors.extract("a.pdf", pdf) == direct
    assert extractors.extract("a.pdf", pdf) == direct  # the second time from the cache


# ---------------------------------------------------------------- graph triples


@pytest.fixture
def triples(monkeypatch):
    """graph.index with the model counted, the cache in a dict and the graph tables written nowhere."""
    rows, asked = FakeVectorRows(), []
    db = SimpleNamespace(execute=lambda *a, **k: None, scalar=lambda *a, **k: 1)
    monkeypatch.setattr(graph, "SessionLocal", SimpleNamespace(begin=lambda: nullcontext(db)))
    monkeypatch.setattr(graph, "cache", rows)
    monkeypatch.setattr(graph, "extract", lambda text: asked.append(text) or {
        "entities": [], "relations": [{"source": "Ravi", "relation": "works on", "target": text}]})
    return SimpleNamespace(rows=rows, asked=asked)


def chunks(*texts):
    return [Document(page_content=t) for t in texts]


def test_reindexing_only_asks_the_model_about_changed_chunks(triples):
    graph.index("doc1", chunks("Atlas", "Borealis"))
    assert graph.index("doc2", chunks("Atlas", "Borealis, edited")) == 2  # both chunks' relations stored
    assert triples.asked == ["Atlas", "Borealis", "Borealis, edited"]


def test_a_reply_that_was_not_json_is_asked_again(triples, monkeypatch):
    monkeypatch.setattr(graph, "extract", lambda text: triples.asked.append(text) or {})
    graph.index("doc1", chunks("Atlas"))
    graph.index("doc1", chunks("Atlas"))
    assert triples.asked == ["Atlas", "Atlas"] and triples.rows.rows == {}


def test_erasure_forgets_a_files_pages_and_triples(triples, monkeypatch):
    pages = FakeVectorRows()
    monkeypatch.setattr(extractors, "page_cache", lambda: PageCache("basic:test:v1", pages))
    monkeypatch.setattr(graph, "GRAPH_RAG", True)
    pdf = make_pdf(["Ravi works on Project Atlas"])
    docs, _ = kb._chunks("doc1", "team.pdf", pdf, ["public"])  # what indexing would have cached
    graph.index("doc1", docs)
    assert pages.rows and triples.rows.rows

    kb.forget_cached("team.pdf", pdf)
    assert pages.rows == {} and triples.rows.rows == {}
