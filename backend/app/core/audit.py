"""Audit log: who did what, and when. Separate from debugging logs so it can be kept longer,
shipped to a separate place, or alerted on (e.g. many login.failure events from one IP).

    audit("document.delete", doc_id="ab12")

Each event is one line on the "app.audit" logger with `event` plus the given fields. The
request id and the logged-in user id are added automatically (see app/core/logging.py).
Event names are `area.action[.outcome]`; never pass passwords or tokens (they'd be redacted anyway).
"""

import logging

_log = logging.getLogger("app.audit")


def audit(event: str, **fields) -> None:
    text = " ".join(f"{k}={v}" for k, v in fields.items())
    _log.info("%s %s", event, text, extra={"fields": {"event": event, **fields}})
