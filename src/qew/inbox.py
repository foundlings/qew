"""A person's inbox: replies to them, approvals they wait on, and work assigned to them."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from qew.claims import is_marker_comment
from qew.github import fetch_inbox_assigned, fetch_inbox_replies, fetch_inbox_waiting, fetch_viewer_login
from qew.moments import format_instant, parse_since

EXCERPT_LIMIT = 100
DEFAULT_WINDOW = timedelta(days=7)


def since_or_default(text: Optional[str]) -> datetime:
    """The --since instant, or seven days before now (UTC); no state is kept."""
    if text is None:
        return datetime.now(timezone.utc).replace(microsecond=0) - DEFAULT_WINDOW
    return parse_since(text)


def build(repository: str, login: Optional[str], since: datetime) -> Dict[str, Any]:
    login = login if login is not None else fetch_viewer_login()
    replies = _reply_items(fetch_inbox_replies(repository, login, since), login, since)
    waiting = fetch_inbox_waiting(repository, login)
    assigned = fetch_inbox_assigned(repository, login)
    return {
        "assigned": assigned,
        "login": login,
        "replies": replies,
        "since": format_instant(since),
        "waiting": waiting,
    }


def rows(inbox: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = [
        {"schema_version": 1, "section": "reply", **item} for item in inbox["replies"]
    ]
    for section in ("waiting", "assigned"):
        result.extend({"schema_version": 1, "section": section, **item} for item in inbox[section])
    return result


def count_line(inbox: Dict[str, Any]) -> str:
    replies = inbox["replies"]
    line = f"qew inbox ({inbox['login']}): {len(replies)} issue(s) with replies since {inbox['since']}"
    if replies:
        shown = ", ".join(f"#{item['number']} ({item['replies'][-1]['author']})" for item in replies)
        line += f": {shown}"
    return f"{line}; {len(inbox['waiting'])} waiting for a human; {len(inbox['assigned'])} assigned."


def text_lines(inbox: Dict[str, Any]) -> List[str]:
    lines = [f"Replies since {inbox['since']} ({len(inbox['replies'])})"]
    for item in inbox["replies"]:
        lines.append(f"#{item['number']} {_printable(item['title'])}")
        for reply in item["replies"]:
            lines.append(f"  {reply['author']} {reply['created_at']}  {reply['excerpt']}")
    for heading, key in (("Waiting for a human", "waiting"), ("Assigned", "assigned")):
        lines.append(f"{heading} ({len(inbox[key])})")
        lines.extend(f"#{item['number']} {_printable(item['title'])}" for item in inbox[key])
    return lines


def _reply_items(issues: List[Dict[str, Any]], login: str, since: datetime) -> List[Dict[str, Any]]:
    items = []
    for issue in sorted(issues, key=lambda value: value["number"]):
        replies = [
            {
                "author": comment["author"] or "ghost",
                "created_at": format_instant(comment["created_at"]),
                "excerpt": _excerpt(comment["body"]),
            }
            for comment in issue["comments"]
            if comment["created_at"] >= since and _is_reply(comment, login)
        ]
        if replies:
            items.append(
                {
                    "number": issue["number"],
                    "replies": replies,
                    "title": issue["title"],
                    "url": issue["url"],
                }
            )
    return items


def _is_reply(comment: Dict[str, Any], login: str) -> bool:
    author = comment["author"]
    if author is not None:
        if author.casefold() == login.casefold():
            return False
        if comment["author_type"] == "Bot" or author.endswith("[bot]"):
            return False
    return not is_marker_comment(comment["body"])


def _excerpt(body: str) -> str:
    first = _printable(body.strip().splitlines()[0]) if body.strip() else ""
    if len(first) > EXCERPT_LIMIT:
        return first[: EXCERPT_LIMIT - 1] + "…"
    return first


def _printable(text: str) -> str:
    """GitHub text is data: control characters never reach the terminal."""
    return "".join(character if character.isprintable() else " " for character in text)
