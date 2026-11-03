"""Azure AI Document Intelligence, prebuilt-layout model: OCR, reading order, tables and headings
done in Azure's cloud, returned directly as Markdown. No local models to install.

Setup: create a Document Intelligence resource in the Azure portal and set AZURE_DI_ENDPOINT
(https://<name>.cognitiveservices.azure.com) and AZURE_DI_KEY. It's billed per page, and the
documents leave your network: check that's allowed for confidential files.

The API is asynchronous: POST the file -> 202 Accepted with an Operation-Location URL -> poll that
URL until the status is "succeeded" (seconds for a few pages, longer for big files).
"""

import time

import httpx

from app.core.config import AZURE_DI_ENDPOINT, AZURE_DI_KEY
from app.rag.extractors import Page, UnreadableDocument

API_VERSION = "2024-11-30"
POLL_SECONDS = 2
TIMEOUT_SECONDS = 600


def _analyze(client: httpx.Client, data: bytes) -> dict:
    resp = client.post(
        f"{AZURE_DI_ENDPOINT.rstrip('/')}/documentintelligence/documentModels/prebuilt-layout:analyze",
        params={
            "api-version": API_VERSION,
            "outputContentFormat": "markdown",
            # offsets in Python's units (code points); the default counts differently for emoji etc.
            "stringIndexType": "unicodeCodePoint",
        },
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    if resp.status_code == 400:  # unsupported, corrupt or password-protected file: permanent
        raise UnreadableDocument(f"Document Intelligence rejected the file: {resp.text[:300]}")
    resp.raise_for_status()  # 401/429/5xx: raised as is, so the worker retries the document later

    status_url = resp.headers["Operation-Location"]
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        poll = client.get(status_url)
        poll.raise_for_status()
        body = poll.json()
        if body["status"] == "succeeded":
            return body["analyzeResult"]
        if body["status"] == "failed":
            raise UnreadableDocument(f"Document Intelligence failed: {body.get('error')}")
    raise TimeoutError(f"Document Intelligence still running after {TIMEOUT_SECONDS}s")


def extract(filename: str, data: bytes) -> list[Page]:
    if not (AZURE_DI_ENDPOINT and AZURE_DI_KEY):
        raise RuntimeError("EXTRACTOR=azure needs AZURE_DI_ENDPOINT and AZURE_DI_KEY")
    with httpx.Client(timeout=60, headers={"Ocp-Apim-Subscription-Key": AZURE_DI_KEY}) as client:
        result = _analyze(client, data)

    content = result["content"]  # the whole document as one Markdown string
    # each page lists the spans (offset, length) of `content` that are on it: cut it back into pages
    pages = [
        (page["pageNumber"], "".join(content[s["offset"]: s["offset"] + s["length"]] for s in page.get("spans", [])))
        for page in result.get("pages", [])
    ]
    return pages or [(None, content)]
