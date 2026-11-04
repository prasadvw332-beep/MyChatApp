"""Document permissions (ACL_ENABLED=true): who may see which document.

Each document has an access list of *principals*:
    "public"        everyone who can log in
    "user:42"       one user (the uploader of a private file)
    "group:hr"      everyone in a group (groups come from SSO: the identity provider's groups claim)

A user's principals are "public", their "user:<id>" and a "group:<name>" per group. A document is
visible when the two lists share at least one entry.

The check happens INSIDE the vector search (a Qdrant filter on each chunk's acl), not after it:
filtering afterwards would leave fewer than k results, and a bug there would put a forbidden
chunk in the prompt. The same list is stored on the document row, for the document list and
the file download. Chunks are filtered before the LLM sees them; that's the only place it is
safe to enforce, since the model will repeat whatever is in its context.
"""

from app.core.config import ACL_ENABLED
from app.models import User

PUBLIC = "public"


def get_principals(user: User | None) -> list[str] | None:
    """The principals to filter by, or None when permissions are off (everyone sees everything)."""
    if not ACL_ENABLED:
        return None
    if user is None:
        return [PUBLIC]
    return [PUBLIC, f"user:{user.id}", *(f"group:{g}" for g in (user.groups or []))]


def can_see(acl: list[str] | None, allowed: list[str] | None) -> bool:
    return allowed is None or bool(set(acl or [PUBLIC]) & set(allowed))


def for_upload(user: User, visibility: str, groups: list[str]) -> list[str]:
    """The access list of a new upload. Users can share only with groups they're in themselves."""
    if visibility == "private":
        return [f"user:{user.id}"]
    if visibility == "groups":
        mine = set(user.groups or [])
        chosen = [f"group:{g}" for g in groups if g in mine]
        return [f"user:{user.id}", *chosen]
    return [PUBLIC]
