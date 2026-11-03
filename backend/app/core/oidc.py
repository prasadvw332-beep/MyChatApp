"""Single sign-on with OpenID Connect (Entra ID, Google, Okta, Keycloak, Auth0, ...).

Companies don't want one more password: people log in with their work account, and leaving the
company (account disabled at the identity provider) removes access everywhere at once. The flow
(authorization code + PKCE), as routes/auth.py runs it:

    1. /auth/oidc/login     redirect the browser to the provider's login page, with
                            state  (random, checked on return: the callback answers OUR request; stops CSRF)
                            nonce  (random, must come back inside the ID token: stops token replay)
                            PKCE   code_challenge = SHA-256 of a random code_verifier we keep
                            All three wait in a short-lived signed cookie.
    2. the user logs in at the provider (password, MFA, ... all handled there)
    3. /auth/oidc/callback  the provider redirects back with a one-time ?code=. The backend swaps it,
                            with the client secret and the code_verifier, for tokens (server to server)
    4. verify the ID token  a JWT signed by the provider: signature against its published keys
                            (JWKS), issuer, audience (our client id), expiry, nonce
    5. find or create the user by the token's "sub" (stable id; emails can change), copy the groups
       claim into user.groups (document permissions, app/core/acl.py), set our usual session cookie

Endpoints come from discovery: {OIDC_ISSUER}/.well-known/openid-configuration.
Provider notes: Entra ID puts group *object ids* (GUIDs) in "groups" unless configured to emit
names, and switches to an "overage" claim above 200 groups; Google has no groups claim at all
(Workspace groups need the Directory API).
"""

import base64
import hashlib
import secrets
from functools import cache
from urllib.parse import urlencode

import httpx
import jwt

from app.core import entra
from app.core.config import OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, OIDC_GROUPS_CLAIM, OIDC_ISSUER, OIDC_REDIRECT_URI

FLOW_COOKIE = "oidc_flow"
# Entra: also Graph scopes, so the login's access token can read the user's groups (entra.py)
SCOPES = "openid email profile" + (f" {entra.SCOPES}" if entra.enabled() else "")


def enabled() -> bool:
    return bool(OIDC_ISSUER and OIDC_CLIENT_ID)


@cache
def discovery() -> dict:
    resp = httpx.get(f"{OIDC_ISSUER.rstrip('/')}/.well-known/openid-configuration", timeout=10)
    resp.raise_for_status()
    return resp.json()


@cache
def _jwks() -> jwt.PyJWKClient:
    # fetches the provider's public keys and caches them; a new key id (key rotation) triggers a refetch
    return jwt.PyJWKClient(discovery()["jwks_uri"])


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def start() -> tuple[str, dict]:
    """(URL to send the browser to, flow data to keep until the callback)."""
    flow = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48)}
    params = {
        "response_type": "code", "client_id": OIDC_CLIENT_ID, "redirect_uri": OIDC_REDIRECT_URI,
        "scope": SCOPES, "state": flow["state"], "nonce": flow["nonce"],
        "code_challenge": _b64url(hashlib.sha256(flow["verifier"].encode()).digest()),
        "code_challenge_method": "S256",
    }
    return f"{discovery()['authorization_endpoint']}?{urlencode(params)}", flow


def finish(code: str, flow: dict) -> tuple[dict, str | None]:
    """Swap the code for tokens; return the verified ID token's claims and the access token (for
    provider APIs such as Microsoft Graph). Raises on anything wrong."""
    resp = httpx.post(discovery()["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": OIDC_REDIRECT_URI,
        "client_id": OIDC_CLIENT_ID, "client_secret": OIDC_CLIENT_SECRET, "code_verifier": flow["verifier"],
    }, timeout=10)
    resp.raise_for_status()
    tokens = resp.json()
    id_token = tokens["id_token"]
    key = _jwks().get_signing_key_from_jwt(id_token)
    # Entra multi-tenant: the discovery issuer is a "{tenantid}" template, checked per token instead
    templated = entra.enabled() and entra.multi_tenant()
    claims = jwt.decode(
        id_token, key.key, algorithms=["RS256", "ES256"],  # never "none", never HS*: those would trust anyone
        audience=OIDC_CLIENT_ID, issuer=None if templated else discovery()["issuer"], leeway=30,
        options={"verify_iss": not templated},
    )
    if templated:
        entra.check_tenant(claims)
    if not secrets.compare_digest(claims.get("nonce", ""), flow["nonce"]):
        raise ValueError("nonce mismatch")
    return claims, tokens.get("access_token")


def groups(claims: dict, access_token: str | None = None) -> list[str]:
    if entra.enabled():
        return entra.groups(claims, access_token)  # ids -> names, overage via Graph, app roles
    value = claims.get(OIDC_GROUPS_CLAIM) or []
    return [str(g) for g in value][:200] if isinstance(value, list) else []


def logout_url() -> str | None:
    """The provider's sign-out URL (ends the session there too), if it has one."""
    endpoint = discovery().get("end_session_endpoint")
    return entra.logout_url(endpoint) if endpoint and entra.enabled() else endpoint


def username(claims: dict) -> str:
    """A readable username from the claims: the email's local part, as our username rules allow."""
    raw = (claims.get("preferred_username") or claims.get("email") or claims["sub"]).split("@")[0].lower()
    cleaned = "".join(c for c in raw if c.isalnum() or c in "_.-")[:24]
    return cleaned if len(cleaned) >= 3 else f"user{cleaned}"
