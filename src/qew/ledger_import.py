"""Rebuild ledger items from a forge project, and recreate a forge project from the ledger.

`import_target` reads every issue of a target. An issue carrying qew's body marker restores the item it
was mirrored from (id, kind, priority, labels, parent, needs and state, taken from the footer and the
managed labels); any other issue becomes a fresh draft. Both write mirror files and are idempotent.
`restore_target` repairs mirror files whose remote issue no longer exists and then runs the ordinary
sync, which recreates issues (adopting by marker after a crash), links and board placement.
"""

import re
from collections.abc import Iterable

from qew import label_style, sync_plan
from qew.forge import ForgeError, MARKER_PREFIX, RemoteIssue, Unsupported
from qew.forge_pull import PullReport, clean_body, clean_labels, clean_line, pull_target
from qew.ledger_ids import valid_id
from qew.ledger_item import Kind, from_header
from qew.ledger_store import Ledger, now
from qew.sync import Target

_MARKER = re.compile(r"<!-- " + re.escape(MARKER_PREFIX) + r"([A-Za-z0-9][A-Za-z0-9._-]*) -->")
_FOOTER = re.compile(r"^(\S+) · (\w+) · (\w+)$")
_LINK = re.compile(r"#(\d+)")
RESTORED_QUOTE = "forge-import: restored from the forge's qew state label"


def _priority_of(labels: Iterable[str]) -> str:
    """The item priority from a `P0`..`P4` label, else from the retired `priority:N` spelling, else normal (2)."""
    for label in labels:
        if label_style.is_priority(label):
            return label[1:]
    return _managed(labels, "priority:") or "2"


def _managed(labels: Iterable[str], prefix: str) -> str | None:
    for label in labels:
        if label.startswith(prefix):
            return label[len(prefix):]
    return None


def _footer(body: str) -> tuple[tuple[str, str, str] | None, list[int], list[int]]:
    """(id, state, kind), parent numbers, blocked-by numbers from the footer of a mirrored body."""
    if "\n---\n" not in body:
        return None, [], []
    lines = body.rsplit("\n---\n", 1)[1].strip().splitlines()
    head = _FOOTER.match(lines[0]) if lines else None
    parents: list[int] = []
    needs: list[int] = []
    for line in lines[1:]:
        numbers = [int(n) for n in _LINK.findall(line)]
        if line.startswith("Parent:"):
            parents = numbers
        elif line.startswith("Blocked by:"):
            needs = numbers
    return ((head[1], head[2], head[3]) if head else None), parents, needs


def import_target(ledger: Ledger, target: Target, history: bool = False) -> PullReport:
    report = PullReport(target.slug)
    try:
        remote = target.forge.list_issues()
    except Unsupported as error:
        report.skipped.append(error.capability)
        return report
    except ForgeError as error:
        report.failures.append(error.detail)
        return report
    snapshot = ledger.snapshot()
    known_numbers: dict[int, str] = {}
    for item_id in snapshot.items:
        for record in ledger.load_mirrors(item_id):
            if record["_target"] == target.slug and record.get("kind") == "issue":
                known_numbers[record["number"]] = item_id
    created: dict[int, str] = {}
    pending_links: list[tuple[str, list[int], list[int]]] = []
    for issue in remote:
        if issue.number in known_numbers:
            continue
        marker = _MARKER.search(issue.body)
        footer, parents, needs = _footer(issue.body)
        if marker and footer and valid_id(marker.group(1)) and ledger.aliases().canonical(marker.group(1)) not in snapshot.items:
            item_id = _restore_item(ledger, target, report, issue, marker.group(1), footer, history)
            created[issue.number] = item_id
            pending_links.append((item_id, parents, needs))
    numbers = {**known_numbers, **created}
    for item_id, parents, needs in pending_links:
        item = ledger.load_item(item_id)
        updated = item
        if parents and parents[0] in numbers:
            updated = updated.with_value("parent", numbers[parents[0]])
        resolved = [numbers[n] for n in needs if n in numbers]
        if resolved:
            updated = updated.with_value("needs", " ".join(resolved))
        if updated != item:
            ledger.write_item(updated, overwrite=True)
    # Whatever carries no marker (or is already mirrored) goes through the ordinary pull.
    pulled = pull_target(ledger, target, full=True)
    report.new_items += pulled.new_items
    report.applied += pulled.applied
    report.comments += pulled.comments
    report.divergences += pulled.divergences
    report.failures.extend(pulled.failures)
    return report


def _restore_item(ledger: Ledger, target: Target, report: PullReport, issue: RemoteIssue, item_id: str,
                  footer: tuple[str, str, str], history: bool) -> str:
    state, kind = footer[1], footer[2]
    priority = _priority_of(issue.labels)
    labels = clean_labels(sync_plan.user_labels(issue.labels))
    item = from_header({
        "id": item_id, "kind": kind if kind in [k.value for k in Kind] else "issue",
        "title": clean_line(issue.title) or item_id, "parent": "", "needs": "", "labels": " ".join(labels),
        "owner": "", "priority": priority if priority.isdigit() else "2", "created": now(),
    }, clean_body(sync_plan.body_from_remote(issue.body)), "restored item")
    ledger.write_item(item)
    if state == "draft":
        ledger.append_event(item_id, "drafted", provenance="forge-import")
    else:
        ledger.append_event(item_id, "approved", quote=RESTORED_QUOTE, provenance="forge-import")
        if state == "claimed":
            ledger.append_event(item_id, "claimed", owner=issue.assignee or "unknown", provenance="forge-import")
        elif state == "completed":
            ledger.append_event(item_id, "completed", provenance="forge-import")
        elif state == "closed":
            ledger.append_event(item_id, "closed", reason="duplicate" if issue.close_reason == "duplicate" else "not_planned",
                                provenance="forge-import")
    seen = sync_plan.seen_of(issue, [])
    ledger.write_mirror(item_id, target.slug, {
        "forge": target.mirror.forge, "url": target.mirror.repo, "kind": "issue", "number": issue.number,
        "node_id": issue.node_id, "issue_url": issue.url, "synced_at": now(), "synced_event": "",
        "synced_hash": "", "linked": [], "seen": seen,
    })
    report.new_items += 1
    return item_id
