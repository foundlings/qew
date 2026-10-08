"""How one item appears in a read: its text row and its machine row."""

import re
from typing import Dict, List

from qew import ledger_graph, untrusted_text
from qew.ledger_store import Snapshot


def text_row(snapshot: Snapshot, item_id: str) -> str:
    """`ID STATE pN kind title [@owner]`; the title is flattened, bounded and quoted or withheld (untrusted_text)."""
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    state = folded.state.value if folded.state else "?"
    suffix = f" @{folded.owner}" if folded.owner else ""
    return f"{item_id} {state} p{item.priority} {item.kind.value} {untrusted_text.show(item.title).text}{suffix}"


def row_data(snapshot: Snapshot, item_id: str, work_state: bool = False) -> Dict[str, object]:
    """The fixed row shape; owner, parent and needs are omitted when empty. `list` adds `work_state`."""
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    hidden = untrusted_text.findings(item.title)
    row: Dict[str, object] = {
        "id": item_id,
        "kind": item.kind.value,
        "title": None if hidden else item.title,
        "state": folded.state.value if folded.state else None,
        "priority": item.priority,
        "labels": sorted(item.labels),
    }
    if hidden:
        row["text_findings"] = list(hidden)
    if work_state:
        row["work_state"] = ledger_graph.work_state(snapshot, item_id)
    if folded.owner:
        row["owner"] = folded.owner
    if item.parent:
        row["parent"] = item.parent
    if item.needs:
        row["needs"] = list(item.needs)
    for key in ("tracks", "pr"):
        if getattr(item, key):
            row[key] = getattr(item, key)
    return row


def rows(snapshot: Snapshot, ids: List[str], work_state: bool = False) -> List[Dict[str, object]]:
    return [row_data(snapshot, item_id, work_state) for item_id in ids]


def related(snapshot: Snapshot, item_id: str) -> Dict[str, object]:
    """A neighbouring item as `{id, state, title}`; one that does not exist is `{id, state: "missing"}`."""
    if item_id not in snapshot.items:
        return {"id": item_id, "state": "missing"}
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    hidden = untrusted_text.findings(item.title)
    entry: Dict[str, object] = {
        "id": item_id,
        "state": folded.state.value if folded.state else None,
        "title": None if hidden else item.title,
    }
    if hidden:
        entry["text_findings"] = list(hidden)
    return entry


_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f]+")


def link_urls(snapshot: Snapshot, item_id: str) -> List[Dict[str, object]]:
    """Each header link with the forge URLs of the linked item, read from mirrors/ (forge text: shown only if a plain URL)."""
    item = snapshot.items[item_id]
    links = []
    for key in ("tracks", "pr"):
        target = getattr(item, key)
        if target and target in snapshot.items:
            urls = [m["issue_url"] for m in snapshot.ledger.load_mirrors(target)
                    if isinstance(m.get("issue_url"), str) and _URL.fullmatch(m["issue_url"])]
            links.append({"key": key, "item": target, "urls": urls})
    return links
