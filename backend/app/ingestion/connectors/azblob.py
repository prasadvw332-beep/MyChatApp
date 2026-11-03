"""Files in an Azure Blob Storage container (ADLS Gen2 too: it speaks the same Blob API).

    azblob://<account>/<container>              every blob in the container
    azblob://<account>/<container>/hr           only blobs under hr/ (names stored relative to it)

Setup: in the Azure portal, generate a SAS token for the container (or the storage account)
with only the Read and List permissions, and set AZURE_STORAGE_SAS_TOKEN.

Azure's SDK (azure-storage-blob) would hide the details; plain REST shows what happens
underneath, and it's the same idea as S3: one XML listing call per 5000 blobs, then a GET per
blob. A SAS token is just query parameters, signed by Azure, appended to every URL.
"""

import xml.etree.ElementTree as ET
from functools import partial
from urllib.parse import quote

import httpx

from app.core.config import AZURE_STORAGE_SAS_TOKEN
from app.ingestion.connectors import SourceFile, get


def parse(url: str) -> tuple[str, str, str]:
    """'azblob://acct/docs/hr' -> ('acct', 'docs', 'hr/')."""
    account, _, rest = url.removeprefix("azblob://").partition("/")
    container, _, prefix = rest.partition("/")
    if not account or not container:
        raise ValueError(f"expected azblob://<account>/<container>[/prefix], got {url!r}")
    prefix = prefix.strip("/")
    return account, container, f"{prefix}/" if prefix else ""


def _download(client: httpx.Client, url: str) -> bytes:
    return get(client, url).content


def list_files(url: str) -> list[SourceFile]:
    account, container, prefix = parse(url)
    sas = AZURE_STORAGE_SAS_TOKEN.lstrip("?")
    if not sas:
        raise ValueError("set AZURE_STORAGE_SAS_TOKEN (Read + List permissions)")
    base = f"https://{account}.blob.core.windows.net/{container}"
    client = httpx.Client(timeout=60)

    files: list[SourceFile] = []
    marker = ""  # continuation token: where the previous page of the listing ended
    while True:
        listing = f"{base}?restype=container&comp=list&prefix={quote(prefix)}&marker={quote(marker)}&{sas}"
        root = ET.fromstring(get(client, listing).content)
        for blob in root.iter("Blob"):
            name = blob.findtext("Name") or ""
            size = int(blob.findtext("Properties/Content-Length") or 0)
            if name.endswith("/") or not size:  # folder placeholders (ADLS directories) and empty blobs
                continue
            download = partial(_download, client, f"{base}/{quote(name)}?{sas}")
            files.append(SourceFile(name.removeprefix(prefix), size, download))
        marker = root.findtext("NextMarker") or ""
        if not marker:
            return files
