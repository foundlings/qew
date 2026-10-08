"""Pull forge-side changes into the ledger.

Content edits (title, body, labels, comments, new issues) merge as events with provenance `forge`; a
STATE change (closed, reopened, assignee, deleted) never overrides ledger state and is recorded as a
`forge_divergence` for `qew accept` / `qew reject`. The baseline for "changed on the forge" is the
`seen` snapshot of the mirror file, written by every push and pull. Forge text is untrusted data:
titles are flattened to one safe line, bodies lose control characters.
"""

from dataclasses import dataclass, field, replace
import hashlib
import re
import unicodedata
from typing import Dict, List, Optional

from qew import sync_plan
from qew.forge import ForgeError, IssueSpec, RemoteIssue, Unsupported, marker_for
from qew.ledger_item import Item
from qew.ledger_store import Ledger, Snapshot

TITLE_LIMIT = 200
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class PullReport:
    slug: str
    applied: int = 0
    divergences: int = 0
    new_items: int = 0
    comments: int = 0
    duplicates: int = 0
    skipped: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    retry_after: float = 0.0  # seconds a rate-limited forge asked us to wait


def clean_line(text: str) -> str:
    """One printable line: controls, newlines and format characters become spaces, capped in length."""
    flat = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text)
    return " ".join(flat.split())[:TITLE_LIMIT]


def clean_body(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n"))


def clean_labels(labels) -> List[str]:
    return sorted({l for l in labels if l and not any(c.isspace() or c == "," for c in l) and len(l) <= 60})


def pull_target(ledger: Ledger, target) -> PullReport:
    report = PullReport(target.slug)
    try:
        remote = target.forge.list_issues()
    except Unsupported as error:
        report.skipped.append(error.capability)
        return report
    except ForgeError as error:
        report.failures.append(error.detail)
        report.retry_after = getattr(error, "retry_after", 0.0)
        return report
    snapshot = ledger.snapshot()
    by_number = {issue.number: issue for issue in remote}
    mirrored: Dict[int, str] = {}
    for item_id in snapshot.items:
        for record in ledger.load_mirrors(item_id):
            if record["_target"] == target.slug and record.get("kind") == "issue":
                mirrored[record["number"]] = item_id
    try:
        for number, item_id in sorted(mirrored.items()):
            _pull_item(ledger, snapshot, target, report, item_id, by_number.get(number))
        for issue in () if target.read_only else remote:
            if issue.number in mirrored:
                continue
            marker = _MARKER.search(issue.body)
            if marker is None:
                _import_issue(ledger, snapshot, target, report, issue)
            elif marker.group(1) in snapshot.items:
                _close_duplicate(target, report, issue, marker.group(1), mirrored)
    except Unsupported as error:
        report.skipped.append(error.capability)
    except ForgeError as error:
        report.failures.append(error.detail)
        report.retry_after = getattr(error, "retry_after", 0.0)
    return report


_MARKER = re.compile(r"<!-- qew:([A-Za-z0-9][A-Za-z0-9._-]*) -->")


def _close_duplicate(target, report: PullReport, issue: RemoteIssue, item_id: str, mirrored: Dict[int, str]) -> None:
    """A second issue for an item already mirrored to a lower number (concurrent syncs): close it, drop its marker."""
    keeper = min((n for n, i in mirrored.items() if i == item_id), default=None)
    if keeper is None or keeper >= issue.number or not issue.open:
        return
    body = issue.body.replace(marker_for(item_id), "<!-- qew-duplicate -->")
    target.forge.update_issue(issue.number, IssueSpec(issue.title, body, tuple(issue.labels), False, "not_planned", ""))
    report.duplicates += 1


def _record(ledger: Ledger, item_id: str, slug: str) -> dict:
    return next(m for m in ledger.load_mirrors(item_id) if m["_target"] == slug)


def _save(ledger: Ledger, item_id: str, slug: str, record: dict, seen: dict) -> None:
    clean = {k: v for k, v in record.items() if k != "_target"}
    ledger.write_mirror(item_id, slug, {**clean, "seen": seen})


def _already_open(snapshot: Snapshot, item_id: str, field_name: str, forge_value: str) -> bool:
    return any(e.get("field") == field_name and e.get("forge") == forge_value for e in snapshot.folds[item_id].divergences)


def _diverge(ledger: Ledger, snapshot: Snapshot, target, report: PullReport, item_id: str, number: int,
             field_name: str, forge_value: str, ledger_value: str) -> None:
    if _already_open(snapshot, item_id, field_name, forge_value):
        return
    ledger.append_event(item_id, "forge_divergence", target=target.slug, field=field_name, forge=forge_value,
                        ledger=ledger_value, remote_ref=str(number), provenance="forge")
    report.divergences += 1


def _apply_header(ledger: Ledger, item: Item, key: str, value: str) -> Item:
    old = item.header()[key]
    updated = item.with_value(key, value)
    ledger.write_item(updated, overwrite=True)
    ledger.append_event(item.id, "field", key=key, old=old, new=value, provenance="forge")
    return updated


def _pull_item(ledger: Ledger, snapshot: Snapshot, target, report: PullReport, item_id: str,
               issue: Optional[RemoteIssue]) -> None:
    record = _record(ledger, item_id, target.slug)
    seen = record.get("seen")
    if seen is not None:  # baselines written before a prefix became managed may still hold such labels
        seen = {**seen, "labels": sync_plan.user_labels(seen.get("labels", []))}
    item = snapshot.items[item_id]
    if issue is None:
        _diverge(ledger, snapshot, target, report, item_id, record["number"], "exists", "missing", "present")
        return
    current = sync_plan.seen_of(issue, (seen or {}).get("comments", []))
    if seen is None:
        # No baseline on this machine (new checkout, or a mirror from before pulling existed): we cannot tell who changed
        # what, so a difference is a divergence for a human to settle, never something a later push overwrites.
        title = clean_line(issue.title)
        if title != item.title:
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "title", title, item.title)
        labels = clean_labels(current["labels"])
        if labels != sorted(item.labels):
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "labels", " ".join(labels), " ".join(item.labels))
        _save(ledger, item_id, target.slug, {**record, "baselined": True}, current)
        return
    if target.read_only:
        _pull_state_only(ledger, snapshot, target, report, item_id, record, issue, seen, current)
        return
    changed = False
    title = clean_line(issue.title)
    if title != seen["title"]:
        if item.title == seen["title"] and title:
            item = _apply_header(ledger, item, "title", title)
            report.applied += 1
        else:
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "title", title, item.title)
        changed = True
    body = clean_body(current["body"])
    if body != seen["body"]:
        if item.body.strip() == seen["body"]:
            ledger.write_item(replace(item, body=body), overwrite=True)
            ledger.append_event(item_id, "field", key="body", old="", new="(changed on the forge)", provenance="forge")
            item = replace(item, body=body)
            report.applied += 1
        else:
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "body", "(edited)", "(edited)")
        changed = True
    labels = clean_labels(current["labels"])
    if labels != seen["labels"]:
        if sorted(item.labels) == seen["labels"]:
            item = _apply_header(ledger, item, "labels", " ".join(labels))
            report.applied += 1
        else:
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "labels", " ".join(labels), " ".join(item.labels))
        changed = True
    if issue.open != seen["open"]:
        _diverge(ledger, snapshot, target, report, item_id, issue.number, "state",
                 "open" if issue.open else "closed", "open" if seen["open"] else "closed")
        changed = True
    if issue.assignee != seen["assignee"]:
        _diverge(ledger, snapshot, target, report, item_id, issue.number, "assignee", issue.assignee or "(none)",
                 snapshot.folds[item_id].owner or "(none)")
        changed = True
    comment_ids = list(seen["comments"])
    if issue.comments > len(comment_ids):
        known = {e.get("remote_ref") for e in snapshot.events[item_id] if e.type == "commented"}
        for comment in target.forge.list_comments(issue.number):
            if comment.id not in comment_ids:
                comment_ids.append(comment.id)
                if f"{target.slug}#{issue.number}/{comment.id}" not in known:
                    ledger.append_event(item_id, "commented", text=clean_body(comment.body), author=clean_line(comment.author),
                                        remote_ref=f"{target.slug}#{issue.number}/{comment.id}", provenance="forge")
                    report.comments += 1
        changed = True
    if changed:
        _save(ledger, item_id, target.slug, record, {**current, "title": title, "body": body, "labels": labels,
                                                      "comments": comment_ids})


def _pull_state_only(ledger: Ledger, snapshot: Snapshot, target, report: PullReport, item_id: str, record: dict,
                     issue: RemoteIssue, seen: dict, current: dict) -> None:
    """A one-way mirror: edits made there are not imported; only a close or reopen is flagged."""
    if issue.open == seen["open"]:
        return
    _diverge(ledger, snapshot, target, report, item_id, issue.number, "state",
             "open" if issue.open else "closed", "open" if seen["open"] else "closed")
    _save(ledger, item_id, target.slug, record, {**seen, "open": issue.open})


def _import_issue(ledger: Ledger, snapshot: Snapshot, target, report: PullReport, issue: RemoteIssue) -> None:
    from qew.ledger_item import from_header
    from qew.ledger_store import now

    known = list(snapshot.items)
    item_id = ledger.new_id("issue", known)
    labels = clean_labels(sync_plan.user_labels(issue.labels))
    item = from_header({
        "id": item_id, "kind": "issue", "title": clean_line(issue.title) or f"Imported issue {issue.number}",
        "parent": "", "needs": "", "labels": " ".join(labels), "owner": "", "priority": "2", "created": now(),
    }, clean_body(issue.body), "imported issue")
    ledger.write_item(item)
    ledger.append_event(item_id, "drafted", provenance="forge-import")
    if not issue.open:
        ledger.append_event(item_id, "closed", reason="not_planned", provenance="forge-import")
    seen = sync_plan.seen_of(issue, [])
    seen = {**seen, "title": item.title, "body": item.body.strip(), "labels": labels}
    ledger.write_mirror(item_id, target.slug, {
        "forge": target.mirror.forge, "url": target.mirror.repo, "kind": "issue", "number": issue.number,
        "node_id": issue.node_id, "issue_url": issue.url, "synced_at": now(),
        "synced_event": "", "synced_hash": "", "linked": [], "seen": seen,
    })
    report.new_items += 1
