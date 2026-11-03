"""Messages of a Slack channel, one Markdown file per day, with thread replies under their message.

    slack://C0123456789     the channel id (channel details > About, at the bottom)

Setup: create a Slack app, give its bot token the scopes channels:history, channels:read and
users:read (groups:history and groups:read for private channels), install it to the workspace,
invite the bot to the channel (/invite @yourbot) and set SLACK_BOT_TOKEN (xoxb-...).
SLACK_HISTORY_DAYS (default 90) sets how far back the import goes.

Files are named "<channel>/<date>.md": tonight's import replaces today's file with the full day
once more messages came in, and older days are skipped as unchanged (same content hash).
"""

import re
import time
from datetime import UTC, datetime

import httpx

from app.core.config import SLACK_BOT_TOKEN, SLACK_HISTORY_DAYS
from app.ingestion.connectors import SourceFile, get

SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "bot_add"}


def _call(client: httpx.Client, method: str, **params) -> dict:
    # Slack answers 200 even for errors; whether it worked is in "ok"
    body = get(client, f"/{method}", params=params).json()
    if not body.get("ok"):
        raise ValueError(f"Slack {method}: {body.get('error')}")
    return body


def _pages(client: httpx.Client, method: str, key: str, **params):
    """Every item of a paged Slack method (cursor pagination)."""
    while True:
        body = _call(client, method, limit=200, **params)
        yield from body.get(key, [])
        cursor = body.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return
        params["cursor"] = cursor


def _clean(text: str, users: dict[str, str]) -> str:
    """Slack's markup -> readable text: <@U123> -> @Name, <https://x|label> -> label."""
    text = re.sub(r"<@(\w+)>", lambda m: "@" + users.get(m.group(1), m.group(1)), text)
    text = re.sub(r"<(?:[^|>]+)\|([^>]+)>", r"\1", text)
    return re.sub(r"<([^>]+)>", r"\1", text)


def _line(msg: dict, users: dict[str, str], reply: bool) -> str:
    when = datetime.fromtimestamp(float(msg["ts"]), UTC).strftime("%H:%M")
    who = users.get(msg.get("user", ""), msg.get("username") or "unknown")
    return f"{'    ↳ ' if reply else ''}{who} ({when} UTC): {_clean(msg.get('text', ''), users)}"


def list_files(url: str) -> list[SourceFile]:
    channel = url.removeprefix("slack://").strip("/")
    if not re.fullmatch(r"[CG][A-Z0-9]+", channel):
        raise ValueError(f"expected slack://<channel id> (C... or G...), got {url!r}")
    if not SLACK_BOT_TOKEN:
        raise ValueError("set SLACK_BOT_TOKEN")
    client = httpx.Client(base_url="https://slack.com/api", headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                          timeout=60)

    name = _call(client, "conversations.info", channel=channel)["channel"]["name"]
    users = {u["id"]: u.get("real_name") or u["name"] for u in _pages(client, "users.list", "members")}
    oldest = f"{time.time() - SLACK_HISTORY_DAYS * 86400:.0f}"

    days: dict[str, list[str]] = {}  # date -> messages (each with its thread), newest first
    for msg in _pages(client, "conversations.history", "messages", channel=channel, oldest=oldest):
        if msg.get("subtype") in SKIP_SUBTYPES:
            continue
        text = _line(msg, users, reply=False)
        if msg.get("reply_count"):  # a thread: the replies come from their own method, parent first
            replies = list(_pages(client, "conversations.replies", "messages", channel=channel, ts=msg["ts"]))[1:]
            text += "".join("\n" + _line(r, users, reply=True) for r in replies)
        day = datetime.fromtimestamp(float(msg["ts"]), UTC).date().isoformat()
        days.setdefault(day, []).append(text)

    files = []
    for day, messages in sorted(days.items()):
        data = f"# #{name} on {day}\n\n" + "\n\n".join(reversed(messages))  # oldest first, as people read
        data_bytes = data.encode("utf-8")
        files.append(SourceFile(f"{name}/{day}.md", len(data_bytes), lambda d=data_bytes: d))
    return files
