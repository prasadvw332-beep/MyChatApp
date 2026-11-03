"""Files in a SharePoint document library (or a folder in it), read through Microsoft Graph.

    sharepoint://contoso.sharepoint.com/sites/HR                    the site's default library ("Documents")
    sharepoint://contoso.sharepoint.com/sites/HR/Policies/2026      only that folder of it

Teams files work the same way, as they're SharePoint libraries underneath
(sharepoint://contoso.sharepoint.com/teams/<team>).

Setup (Microsoft Entra admin center): register an app, add the Microsoft Graph *application*
permission Sites.Read.All (or Sites.Selected to allow only chosen sites), have an admin grant
consent, create a client secret, and set SHAREPOINT_TENANT_ID, _CLIENT_ID and _CLIENT_SECRET.
The app signs in as itself (client credentials grant), with no user, so it suits scheduled imports.
"""

from functools import partial
from urllib.parse import quote

import httpx

from app.core.config import SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID
from app.ingestion.connectors import CachedToken, SourceFile

GRAPH = "https://graph.microsoft.com/v1.0"


def parse(url: str) -> tuple[str, str, str]:
    """'sharepoint://host/sites/HR/Policies' -> ('host', 'sites/HR', 'Policies')."""
    host, _, rest = url.removeprefix("sharepoint://").partition("/")
    parts = [p for p in rest.split("/") if p]
    if not host or len(parts) < 2 or parts[0] not in ("sites", "teams"):
        raise ValueError(f"expected sharepoint://<tenant>.sharepoint.com/sites/<site>[/folder], got {url!r}")
    return host, f"{parts[0]}/{parts[1]}", "/".join(parts[2:])


def _fetch_token() -> tuple[str, int]:
    if not (SHAREPOINT_TENANT_ID and SHAREPOINT_CLIENT_ID and SHAREPOINT_CLIENT_SECRET):
        raise ValueError("set SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID and SHAREPOINT_CLIENT_SECRET")
    resp = httpx.post(
        f"https://login.microsoftonline.com/{SHAREPOINT_TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": SHAREPOINT_CLIENT_ID,
            "client_secret": SHAREPOINT_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",  # = whatever permissions the app was granted
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    return body["access_token"], body["expires_in"]


def _get(client: httpx.Client, token: CachedToken, url: str) -> httpx.Response:
    resp = client.get(url, headers=token.headers())
    resp.raise_for_status()
    return resp


def _download(client: httpx.Client, token: CachedToken, url: str) -> bytes:
    # /content answers with a redirect to a short-lived, pre-signed URL on another host;
    # httpx follows it and drops our Authorization header on the way, as it should
    return _get(client, token, url).content


def _walk(client: httpx.Client, token: CachedToken, drive: str, item_id: str, path: str,
          files: list[SourceFile]) -> None:
    url = f"/drives/{drive}/items/{item_id}/children?$select=id,name,size,folder,file&$top=999"
    while url:
        page = _get(client, token, url).json()
        for child in page["value"]:
            name = path + child["name"]
            if "folder" in child:
                _walk(client, token, drive, child["id"], f"{name}/", files)
            elif "file" in child:  # neither: OneNote notebooks and other packages, skipped
                download = partial(_download, client, token, f"/drives/{drive}/items/{child['id']}/content")
                files.append(SourceFile(name, child.get("size", 0), download))
        url = page.get("@odata.nextLink")  # the next page as a full URL; absent on the last page


def list_files(url: str) -> list[SourceFile]:
    host, site, folder = parse(url)
    token = CachedToken(_fetch_token)
    client = httpx.Client(base_url=GRAPH, timeout=60, follow_redirects=True)
    # three lookups turn the readable URL into ids: site -> its default library (a "drive") -> the folder
    site_id = _get(client, token, f"/sites/{host}:/{site}").json()["id"]
    drive = _get(client, token, f"/sites/{site_id}/drive").json()["id"]
    # "root:/a/b:" addresses an item by its path inside the library
    start = _get(client, token, f"/drives/{drive}/root" + (f":/{quote(folder)}:" if folder else "")).json()
    files: list[SourceFile] = []
    _walk(client, token, drive, start["id"], "", files)
    return files
