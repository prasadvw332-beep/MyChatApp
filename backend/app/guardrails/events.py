"""What happens when a guardrail finds something: one metric, one audit event, and in enforce mode, action."""

from app.core import metrics
from app.core.audit import audit
from app.core.config import GUARDRAILS_MODE

ACTIVE = GUARDRAILS_MODE != "off"


def trip(check: str, **fields) -> bool:
    """Record a finding. True means act on it (block, drop, withhold); False means monitor mode.
    `fields` go to the audit log: rule names, file names, categories. Never the text itself."""
    enforce = GUARDRAILS_MODE == "enforce"
    action = "blocked" if enforce else "flagged"
    metrics.guardrail_events.labels(check, action).inc()
    audit(f"guardrail.{action}", check=check, **fields)
    return enforce
