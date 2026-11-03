"""Files in a Google Drive folder (My Drive or a shared drive), read with a service account.

    gdrive://<folder id>      the id is the last part of the folder's URL: drive.google.com/drive/folders/<id>

Setup (Google Cloud console): enable the Drive API, create a service account, download its JSON
key and point GOOGLE_SERVICE_ACCOUNT_FILE at it. Then share the Drive folder with the service
account's email (Viewer is enough), the same way you'd share it with a colleague.

Google Docs aren't files: they're exported as .docx and go through the Word extractor (Google
caps exports at 10 MB). Sheets, Slides, Forms and other Google formats are skipped.
"""

import json
import re
import time
from functools import partial

import httpx
import jwt

from app.core.config import GOOGLE_SERVICE_ACCOUNT_FILE
from app.ingestion.connectors import CachedToken, SourceFile

API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_APPS = "application/vnd.google-apps."  # Google's own formats: folder, document, spreadsheet, ...
FOLDER = GOOGLE_APPS + "folder"
GOOGLE_DOC = GOOGLE_APPS + "document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _fetch_token() -> tuple[str, int]:
    """Service-account sign-in (OAuth 2.0 JWT bearer grant): sign a short claim with the key's
    private key and swap it for an access token. No user and no browser involved."""
    if not GOOGLE_SERVICE_ACCOUNT_FILE:
        raise ValueError("set GOOGLE_SERVICE_ACCOUNT_FILE to the service account's JSON key")
    with open(GOOGLE_SERVICE_ACCOUNT_FILE, encoding="utf-8") as f:
        key = json.load(f)
    now = int(time.time())
    claim = {"iss": key["client_email"], "scope": SCOPE, "aud": key["token_uri"], "iat": now, "exp": now + 3600}
    resp = httpx.post(
        key["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt.encode(claim, key["private_key"], algorithm="RS256"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    return body["access_token"], body["expires_in"]


def _get(client: httpx.Client, token: CachedToken, path: str, params: dict) -> httpx.Response:
    resp = client.get(path, params=params, headers=token.headers())
    resp.raise_for_status()
    return resp


def _download(client: httpx.Client, token: CachedToken, path: str, params: dict) -> bytes:
    return _get(client, token, path, params).content


def _children(client: httpx.Client, token: CachedToken, folder_id: str):
    """Everything directly inside a folder, following the pages of the listing."""
    params = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "nextPageToken, files(id, name, mimeType, size)",
        "pageSize": 1000,
        # without these three, folders on shared drives come back empty
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
        "corpora": "allDrives",
    }
    while True:
        body = _get(client, token, "/files", params).json()
        yield from body["files"]
        if "nextPageToken" not in body:
            return
        params["pageToken"] = body["nextPageToken"]


def _walk(client: httpx.Client, token: CachedToken, folder_id: str, path: str, files: list[SourceFile]) -> None:
    for item in _children(client, token, folder_id):
        name = path + item["name"].replace("/", "_")  # Drive allows "/" in names; we use it as the separator
        mime = item["mimeType"]
        if mime == FOLDER:
            _walk(client, token, item["id"], f"{name}/", files)
        elif mime == GOOGLE_DOC:
            # size is unknown until exported (a Doc has no stored file), so 0: never skipped as too big
            export = partial(_download, client, token, f"/files/{item['id']}/export", {"mimeType": DOCX})
            files.append(SourceFile(f"{name}.docx", 0, export))
        elif not mime.startswith(GOOGLE_APPS):  # an uploaded file (PDF, .docx, ...): downloaded as is
            params = {"alt": "media", "supportsAllDrives": True}
            download = partial(_download, client, token, f"/files/{item['id']}", params)
            files.append(SourceFile(name, int(item.get("size", 0)), download))


def list_files(url: str) -> list[SourceFile]:
    folder_id = url.removeprefix("gdrive://").strip("/")
    if not re.fullmatch(r"[\w-]+", folder_id):  # it goes into the query string above
        raise ValueError(f"expected gdrive://<folder id>, got {url!r}")
    client = httpx.Client(base_url=API, timeout=60)
    files: list[SourceFile] = []
    _walk(client, CachedToken(_fetch_token), folder_id, "", files)
    return files
