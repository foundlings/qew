"""Pull forge-side changes into the ledger.

Content edits (title, body, labels, comments, new issues) merge as events with provenance `forge`; a
STATE change (closed, reopened, assignee, deleted) never overrides ledger state and is recorded as a
`forge_divergence` for `qew accept` / `qew reject`. The baseline for "changed on the forge" is the
`seen` snapshot of the mirror file, written by every push and pull. Forge text is untrusted data:
titles are flattened to one safe line, bodies lose control characters.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable

from qew import instants, page_cache, pull_cursor, sync_plan
from qew.mirror_types import MirrorRecord, Seen, updated
from qew.forge import DUPLICATE_MARKER, ForgeError, ForgeRejected, IssueSpec, RemoteIssue, Unsupported, marker_for
from qew.ledger_event import State
from qew.ledger_item import Item
from qew.ledger_store import Ledger, Snapshot
from qew.sync import Target, new_record

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
    recreated: int = 0  # mirrors forgotten because the forge repo was wiped; the push recreates their issues
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    retry_after: float = 0.0  # seconds a rate-limited forge asked us to wait
    mode: str = ""  # how the issues were listed: conditional, incremental or full
    pages_fetched: int = 0  # conditional listing: pages that came over the wire
    pages_reused: int = 0  # conditional listing: pages answered 304


def clean_line(text: str) -> str:
    """One printable line: controls, newlines and format characters become spaces, capped in length."""
    flat = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text)
    return " ".join(flat.split())[:TITLE_LIMIT]


def clean_body(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n"))


def clean_labels(labels: Iterable[str]) -> list[str]:
    return sorted({l for l in labels if l and not any(c.isspace() or c == "," for c in l) and len(l) <= 60})


def _list(ledger: Ledger, target: Target, report: PullReport, cursor: pull_cursor.Cursor | None, started: datetime,
          full: bool) -> tuple[list[RemoteIssue], bool]:
    """(issues, complete): the whole set through conditional pages when the forge sends ETags, else a `since` listing."""
    cached = {} if full else page_cache.load(ledger.read_page_files(target.slug))
    try:
        listing = target.forge.list_issue_pages(cached)
    except Unsupported as error:
        if error.capability != "conditional_listing":
            raise
    else:
        ledger.write_page_files(target.slug, page_cache.dump(listing.pages))
        report.mode, report.pages_fetched, report.pages_reused = "conditional", listing.fetched, listing.reused
        return listing.issues(), True
    since = pull_cursor.since_of(cursor, started, full)
    report.mode = "incremental" if since else "full"
    return target.forge.list_issues(since), not since


def wiped(mirrored: Collection[int], remote_numbers: Collection[int]) -> bool:
    """A complete listing holding none of at least two mirrored issues is a deleted and recreated repository.

    One missing issue stays a divergence for a human; the ledger is the source of truth, so recreating many is safe.
    """
    return len(mirrored) >= 2 and not set(mirrored) & set(remote_numbers)


def pull_target(ledger: Ledger, target: Target, full: bool = False, clock: Callable[[], datetime] = instants.now) -> PullReport:
    """Pull one target. `full` ignores the cursor and every cached page. The cursor advances only when the whole pull succeeded."""
    report = PullReport(target.slug)
    started = clock()
    cursor = pull_cursor.parse(ledger.read_cursor(target.slug))
    try:
        remote, complete = _list(ledger, target, report, cursor, started, full)
    except Unsupported as error:
        report.skipped.append(error.capability)
        return report
    except ForgeError as error:
        report.failures.append(error.detail)
        report.retry_after = getattr(error, "retry_after", 0.0)
        return report
    snapshot = ledger.snapshot()
    aliases = ledger.aliases()
    by_number = {issue.number: issue for issue in remote}
    mirrored: dict[int, str] = {}
    for item_id in snapshot.items:
        for record in ledger.load_mirrors(item_id):
            if record["_target"] == target.slug and record.get("kind") == "issue":
                mirrored[record["number"]] = item_id
    unnoticed = {n: i for n, i in mirrored.items()  # an issue whose deletion was already noticed is a deliberate one
                 if not any(d.get("field") == "exists" for d in snapshot.folds[i].divergences)}
    if complete and wiped(unnoticed, by_number):
        for item_id in sorted(set(mirrored.values())):
            ledger.remove_mirror(item_id, target.slug)
        report.recreated, mirrored = len(mirrored), {}
    try:
        for number, item_id in sorted(mirrored.items()):
            issue = by_number.get(number)
            if issue is None and not complete:
                continue  # unchanged since the cursor; only the complete set can show a deletion
            _pull_item(ledger, snapshot, target, report, item_id, issue)
        twins: dict[str, list[tuple[RemoteIssue, str]]] = {}
        for issue in () if target.read_only else remote:
            if issue.number in mirrored:
                continue
            marker = _MARKER.search(issue.body)
            if marker is None:
                _import_issue(ledger, snapshot, target, report, issue)
            elif aliases.canonical(marker.group(1)) in snapshot.items:  # an old marker names the item it was renamed to
                twins.setdefault(aliases.canonical(marker.group(1)), []).append((issue, marker.group(1)))
        for item_id, found in sorted(twins.items()):
            _settle_twins(ledger, target, report, item_id, found, mirrored, snapshot, started, complete)
    except Unsupported as error:
        report.skipped.append(error.capability)
    except ForgeError as error:
        report.failures.append(error.detail)
        report.retry_after = getattr(error, "retry_after", 0.0)
    if not report.failures and not report.skipped:
        ledger.write_cursor(target.slug, pull_cursor.after_pull(cursor, started, report.mode).to_json())
    if full:
        report.mode = "full"
    return report


def pull_items(ledger: Ledger, target: Target, item_ids: Iterable[str]) -> PullReport:
    """Pull only the named items' issues (one read each): forge-side edits land before the push can overwrite them.

    New issues and deletions are the full pull's job; this is the cheap pass that follows a write.
    """
    report = PullReport(target.slug)
    report.mode = "items"
    snapshot = ledger.snapshot()
    try:
        for item_id in sorted(set(item_ids) & set(snapshot.items)):
            for record in ledger.load_mirrors(item_id):
                if record["_target"] == target.slug and record.get("kind") == "issue":
                    try:
                        issue = target.forge.read_issue(record["number"])
                    except ForgeRejected as error:
                        if error.status != 404:
                            raise
                        continue  # a vanished issue is reported by the next full pull
                    _pull_item(ledger, snapshot, target, report, item_id, issue)
    except Unsupported as error:
        report.skipped.append(error.capability)
    except ForgeError as error:
        report.failures.append(error.detail)
        report.retry_after = getattr(error, "retry_after", 0.0)
    return report


_MARKER = re.compile(r"<!-- qew:([A-Za-z0-9][A-Za-z0-9._-]*) -->")


DUPLICATE_GRACE_SECONDS = 600


def _settle_twins(ledger: Ledger, target: Target, report: PullReport, item_id: str, found: list[tuple[RemoteIssue, str]],
                  mirrored: dict[int, str], snapshot: Snapshot, started: datetime, complete: bool) -> None:
    """Issues carrying the marker of an item but not mirrored for it (a crash orphan, another checkout's create).

    The mirror is the authority: an open twin above the mirrored issue closes at once. One below it closes too, but only
    once the mirror is old enough that no merge of a rival checkout's mirror can still be in flight (two checkouts that
    each closed the other's issue would close both). An item with no mirror adopts its lowest-numbered open marker
    issue (when the listing is complete) and closes the rest; sync then brings the adopted issue up to date.
    """
    keeper = min((n for n, i in mirrored.items() if i == item_id), default=None)
    finished = snapshot.folds[item_id].state in (State.COMPLETED, State.CLOSED)
    open_twins = sorted(((i, named) for i, named in found if i.open), key=lambda pair: pair[0].number)
    if keeper is None:
        if not complete or not open_twins:
            return
        winner = open_twins[0][0]
        ledger.write_mirror(item_id, target.slug, new_record(target, winner, snapshot, item_id, ""))
        mirrored[winner.number] = item_id
        open_twins = open_twins[1:]
        keeper = winner.number
        settled = True
    else:
        settled = _mirror_is_settled(ledger, item_id, target.slug, started)
    for issue, named in open_twins:
        if issue.number > keeper or finished or settled:
            body = issue.body.replace(marker_for(named), DUPLICATE_MARKER)
            target.forge.update_issue(issue.number, IssueSpec(issue.title, body, tuple(issue.labels), False, "duplicate", ""))
            report.duplicates += 1


def _mirror_is_settled(ledger: Ledger, item_id: str, slug: str, started: datetime) -> bool:
    """True once the recorded mirror is older than the grace; a mirror without a local sync time (a fresh clone) is settled."""
    record = next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug and m.get("kind") == "issue"), None)
    at = instants.parse_instant(str(record.get("synced_at") or "") if record else "")
    return at is None or (started - at).total_seconds() > DUPLICATE_GRACE_SECONDS


def _record(ledger: Ledger, item_id: str, slug: str) -> MirrorRecord:
    return next(m for m in ledger.load_mirrors(item_id) if m["_target"] == slug)


def _save(ledger: Ledger, item_id: str, slug: str, record: MirrorRecord, seen: Seen) -> None:
    ledger.write_mirror(item_id, slug, updated(record, seen=seen))


def _already_open(snapshot: Snapshot, item_id: str, field_name: str, forge_value: str) -> bool:
    return any(e.get("field") == field_name and e.get("forge") == forge_value for e in snapshot.folds[item_id].divergences)


def _diverge(ledger: Ledger, snapshot: Snapshot, target: Target, report: PullReport, item_id: str, number: int,
             field_name: str, forge_value: str, ledger_value: str, close_reason: str = "") -> None:
    if _already_open(snapshot, item_id, field_name, forge_value):
        return
    extra = {"forge_reason": close_reason} if close_reason else {}
    ledger.append_event(item_id, "forge_divergence", target=target.slug, field=field_name, forge=forge_value,
                        ledger=ledger_value, remote_ref=str(number), provenance="forge", **extra)
    report.divergences += 1


def _apply_header(ledger: Ledger, item: Item, key: str, value: str) -> Item:
    old = item.header()[key]
    updated = item.with_value(key, value)
    ledger.write_item(updated, overwrite=True)
    ledger.append_event(item.id, "field", key=key, old=old, new=value, provenance="forge")
    return updated


def _pull_item(ledger: Ledger, snapshot: Snapshot, target: Target, report: PullReport, item_id: str,
               issue: RemoteIssue | None) -> None:
    record = _record(ledger, item_id, target.slug)
    seen = record.get("seen")
    if seen is not None:  # baselines written before a prefix became managed may still hold such labels
        seen = {**seen, "labels": sync_plan.user_labels(seen.get("labels", []))}
    item = snapshot.items[item_id]
    if issue is None:
        _diverge(ledger, snapshot, target, report, item_id, record["number"], "exists", "missing", "present")
        return
    for stale in snapshot.folds[item_id].divergences:  # the issue is back: a missing-issue divergence is over
        if stale.get("field") == "exists":
            ledger.append_event(item_id, "divergence_rejected", ref=stale.id, reason="the issue exists on the forge again")
    current = sync_plan.seen_of(issue, seen["comments"] if seen else [])
    if seen is None:
        # No baseline on this machine (new checkout, or a mirror from before pulling existed): we cannot tell who changed
        # what, so a difference is a divergence for a human to settle, never something a later push overwrites.
        title = clean_line(issue.title)
        if title != item.title:
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "title", title, item.title)
        labels = clean_labels(current["labels"])
        if labels != sorted(item.labels):
            _diverge(ledger, snapshot, target, report, item_id, issue.number, "labels", " ".join(labels), " ".join(item.labels))
        _save(ledger, item_id, target.slug, updated(record, baselined=True), current)
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
                 "open" if issue.open else "closed", "open" if seen["open"] else "closed", issue.close_reason)
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


def _pull_state_only(ledger: Ledger, snapshot: Snapshot, target: Target, report: PullReport, item_id: str, record: MirrorRecord,
                     issue: RemoteIssue, seen: Seen, current: Seen) -> None:
    """A one-way mirror: edits made there are not imported; only a close or reopen is flagged."""
    if issue.open == seen["open"]:
        return
    _diverge(ledger, snapshot, target, report, item_id, issue.number, "state",
             "open" if issue.open else "closed", "open" if seen["open"] else "closed", issue.close_reason)
    _save(ledger, item_id, target.slug, record, {**seen, "open": issue.open})


CLOSED_ON_FORGE_QUOTE = "forge-import: the issue was closed on the forge before it reached the ledger"


def append_forge_close(ledger: Ledger, item_id: str, reason: str) -> None:
    """Record a close the forge made with the reason the forge gave: not planned and duplicate stay closes, anything else
    (completed, or no reason, which is how GitHub shows a plain close) is completed work, never invented as not planned."""
    if reason in ("not_planned", "duplicate"):
        ledger.append_event(item_id, "closed", reason=reason, provenance="forge-import")
        return
    ledger.append_event(item_id, "approved", quote=CLOSED_ON_FORGE_QUOTE, provenance="forge-import")
    ledger.append_event(item_id, "completed", provenance="forge-import")


def _import_issue(ledger: Ledger, snapshot: Snapshot, target: Target, report: PullReport, issue: RemoteIssue) -> None:
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
        append_forge_close(ledger, item_id, issue.close_reason)
    seen = sync_plan.seen_of(issue, [])
    seen = {**seen, "title": item.title, "body": item.body.strip(), "labels": labels}
    ledger.write_mirror(item_id, target.slug, {
        "forge": target.mirror.forge, "url": target.mirror.repo, "kind": "issue", "number": issue.number,
        "node_id": issue.node_id, "issue_url": issue.url, "synced_at": now(),
        "synced_event": "", "synced_hash": "", "linked": [], "seen": seen,
    })
    report.new_items += 1
