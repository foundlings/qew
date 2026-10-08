"""How one item appears in a read: its text row and its machine row."""

from typing import Dict, List

from qew import stale_claims, untrusted_text
from qew.ledger_event import State
from qew.ledger_store import Snapshot


def text_row(snapshot: Snapshot, item_id: str) -> str:
    """`ID STATE pN kind title [@owner]`; the title is flattened, bounded and quoted or withheld (untrusted_text)."""
    item = snapshot.items[item_id]
    derived, holders = snapshot.derived[item_id]  # an epic is in progress while a direct child is claimed
    state = derived.value if derived else "?"
    suffix = "".join(f" @{holder}" for holder in holders)
    merged = stale_claims.merged_pull_requests(snapshot, item_id)
    if merged:
        suffix += f" [stale: pull request #{merged[0]} merged; qew land {merged[0]}]"
    return f"{item_id} {state} p{item.priority} {item.kind.value} {untrusted_text.show(item.title).text}{suffix}"


def row_data(snapshot: Snapshot, item_id: str) -> Dict[str, object]:
    """The fixed row shape; owner, parent and needs are omitted when empty."""
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    hidden = untrusted_text.findings(item.title)
    row: Dict[str, object] = {
        "id": item_id,
        "kind": item.kind.value,
        "title": None if hidden else item.title,
        "state": snapshot.derived[item_id][0].value if snapshot.derived[item_id][0] else None,
        "priority": item.priority,
        "labels": sorted(item.labels),
    }
    if hidden:
        row["text_findings"] = list(hidden)
    if folded.state == State.CLAIMED:
        row["stale"] = bool(stale_claims.merged_pull_requests(snapshot, item_id))
    if folded.owner:
        row["owner"] = folded.owner
    if item.kind.value == "epic" and snapshot.derived[item_id][1]:
        row["holders"] = list(snapshot.derived[item_id][1])
    if item.parent:
        row["parent"] = item.parent
    if item.needs:
        row["needs"] = list(item.needs)
    return row


def rows(snapshot: Snapshot, ids: List[str]) -> List[Dict[str, object]]:
    return [row_data(snapshot, item_id) for item_id in ids]
