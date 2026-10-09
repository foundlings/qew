"""Findings per source of an item's text: sparse objects for machines, bracketed notes for people.

A finding is `{"source": "body", "zero_width": true}`; comments and labels also carry their `index`
(and a comment its `author`). Text is annotated, never rewritten. The kinds come from untrusted_text.
"""


from qew import textscan, untrusted_text
from qew.ledger_store import Snapshot

_WORDS = {**{kind: textscan.describe(kind) for kind in textscan.KINDS}, "control": "contains control characters"}


def sparse(source: str, text: str, **where: object) -> dict[str, object] | None:
    kinds = untrusted_text.findings(text)
    if not kinds:
        return None
    return {"source": source, **where, **{kind: True for kind in kinds}}


def title_findings(title: str) -> list[dict[str, object]]:
    found = sparse("title", title)
    return [found] if found else []


def comments_of(snapshot: Snapshot, item_id: str) -> list[dict[str, object]]:
    """The item's comments in order: `{index, author, at, text}`."""
    found = [e for e in sorted(snapshot.events[item_id], key=lambda e: e.id) if e.type == "commented"]
    return [{"index": n, "author": e.get("author") or e.actor, "at": e.at, "text": e.get("text")} for n, e in enumerate(found, 1)]


def item_findings(snapshot: Snapshot, item_id: str, *, comments: bool = True, body: bool = True) -> list[dict[str, object]]:
    item = snapshot.items[item_id]
    found = title_findings(item.title)
    if body:
        found += [f for f in (sparse("body", item.body),) if f]
    found += [f for n, label in enumerate(item.labels, 1) for f in (sparse("label", label, index=n),) if f]
    if comments:
        for entry in comments_of(snapshot, item_id):
            hit = sparse("comment", str(entry["text"]), index=entry["index"], author=entry["author"])
            if hit:
                found.append(hit)
    return found


def kinds_of(finding: dict[str, object]) -> list[str]:
    return sorted(key for key, value in finding.items() if value is True)


def where(finding: dict[str, object]) -> str:
    """`title`, `body`, `label 2` or `comment 1 (@author)`."""
    source = str(finding["source"])
    if "index" not in finding:
        return source
    suffix = f" (@{finding['author']})" if "author" in finding else ""
    return f"{source} {finding['index']}{suffix}"


def describe(finding: dict[str, object]) -> str:
    return ", ".join(_WORDS[kind] for kind in kinds_of(finding))


def note(findings: list[dict[str, object]]) -> str:
    """`[title: contains confusable characters; body: contains zero-width characters]`, or empty."""
    if not findings:
        return ""
    return "[" + "; ".join(f"{where(f)}: {describe(f)}" for f in findings) + "]"
