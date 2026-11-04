"""Microsoft Entra ID (formerly Azure AD) specifics for single sign-on.

The login itself is standard OpenID Connect (oidc.py). Entra adds a few things you have to handle:

1. Tenants. ENTRA_TENANT_ID=<your tenant id> lets only your organisation in (single-tenant: the
   usual choice for an internal app). ENTRA_TENANT_ID=organizations accepts any work account, and
   then the issuer in the discovery document is a template ("https://login.microsoftonline.com/
   {tenantid}/v2.0"): every token says which tenant it's from ("tid"), and that tenant must be in
   ENTRA_ALLOWED_TENANTS, or a stranger's company could log in.

2. Groups are ids, not names. With "groups" enabled in the app registration (Token configuration >
   Add groups claim), the ID token lists group *object ids* (GUIDs). ENTRA_GROUP_NAMES maps the
   ones you care about to readable names ("<guid>=hr,<guid>=finance"), which document ACLs use as
   "group:hr". Unmapped ids are kept as they are ("group:3f1c...") and work in ACLs too.

3. Groups overage. A token can't grow without limit: above 200 groups, Entra leaves "groups" out
   and sets "hasgroups" / "_claim_names" instead, meaning "ask Microsoft Graph". We then call
   /me/transitiveMemberOf with the access token from the same login (scopes User.Read and
   GroupMember.Read.All, admin consent), which also returns display names. "transitive": groups
   inside groups count too.

4. App roles. Roles defined in the app registration (App roles) and assigned to users or groups
   in Enterprise applications arrive in the "roles" claim. They're the cleaner way to grant
   access to *this* app; they're added as "role:<name>" next to the groups.

5. Sign-out. Clearing our cookie ends the session here, but the Entra session would log the
   user straight back in. logout_url() also ends it at Entra (front-channel logout).

App registration (Entra admin center > App registrations > New registration):
    Redirect URI (Web)       OIDC_REDIRECT_URI, e.g. https://chat.contoso.com/api/auth/oidc/callback
    Certificates & secrets   a client secret -> OIDC_CLIENT_SECRET (expires: rotate it before then)
    Token configuration      add the groups claim (Security groups), "Group ID"
    API permissions          Microsoft Graph, delegated: User.Read, GroupMember.Read.All (grant admin consent)
"""

import httpx

from app.core.config import ENTRA_ALLOWED_TENANTS, ENTRA_GROUP_NAMES, ENTRA_TENANT_ID, OIDC_ISSUER, OIDC_REDIRECT_URI

GRAPH = "https://graph.microsoft.com/v1.0"
# Graph scopes requested next to openid/email/profile: the access token from the login can then read the groups
SCOPES = "User.Read GroupMember.Read.All"
MULTI_TENANT = {"organizations", "common"}


def is_enabled() -> bool:
    return bool(ENTRA_TENANT_ID) or "login.microsoftonline.com" in OIDC_ISSUER


def is_multi_tenant() -> bool:
    return ENTRA_TENANT_ID in MULTI_TENANT


def check_tenant(claims: dict) -> None:
    """Multi-tenant logins: the token's issuer must match its own tenant, and the tenant must be allowed."""
    tid = claims.get("tid", "")
    if claims.get("iss") != f"https://login.microsoftonline.com/{tid}/v2.0":
        raise ValueError("issuer does not match the token's tenant")
    if tid not in ENTRA_ALLOWED_TENANTS:
        raise ValueError(f"tenant {tid} is not allowed")


def _group_names() -> dict[str, str]:
    """ENTRA_GROUP_NAMES "<guid>=hr,<guid>=finance" -> {"<guid>": "hr", ...}."""
    pairs = (p.split("=", 1) for p in ENTRA_GROUP_NAMES.split(",") if "=" in p)
    return {guid.strip().lower(): name.strip() for guid, name in pairs}


def _overage(claims: dict) -> bool:
    return bool(claims.get("hasgroups")) or "groups" in (claims.get("_claim_names") or {})


def _graph_groups(access_token: str) -> list[str]:
    """Every group the user is in, nested ones included, by display name (paged, 999 per page)."""
    names: list[str] = []
    url: str | None = f"{GRAPH}/me/transitiveMemberOf/microsoft.graph.group?$select=id,displayName&$top=999"
    with httpx.Client(timeout=15, headers={"Authorization": f"Bearer {access_token}"}) as client:
        while url and len(names) < 1000:
            resp = client.get(url)
            resp.raise_for_status()
            page = resp.json()
            names += [g.get("displayName") or g["id"] for g in page.get("value", [])]
            url = page.get("@odata.nextLink")
    return names


def groups(claims: dict, access_token: str | None) -> list[str]:
    """The user's groups and app roles, as plain names for user.groups (ACLs add the "group:" prefix)."""
    if _overage(claims) and access_token:
        found = _graph_groups(access_token)
    else:
        mapping = _group_names()
        found = [mapping.get(str(g).lower(), str(g)) for g in claims.get("groups") or []]
    roles = [f"role:{r}" for r in claims.get("roles") or []]
    return list(dict.fromkeys(found + roles))[:500]


def logout_url(end_session_endpoint: str) -> str:
    """Where to send the browser to end the Entra session too, and come back to the app afterwards."""
    app_root = OIDC_REDIRECT_URI.split("/api/")[0].split("/auth/")[0] + "/"
    return f"{end_session_endpoint}?post_logout_redirect_uri={httpx.URL(app_root)}"


def subject(claims: dict) -> str:
    """The account key for Entra users: tenant + object id. Entra's "sub" is different in every app
    registration (pairwise), while "oid" is the user's one id in the tenant: the same person in
    Graph, in audit logs and in any other app."""
    return f"{claims['tid']}:{claims['oid']}"
