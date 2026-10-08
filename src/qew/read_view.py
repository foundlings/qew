"""How one item appears in a read: its text row and its machine row."""

from typing import Dict, List

from qew import untrusted_text
from qew.ledger_store import Snapshot


def text_row(snapshot: Snapshot, item_id: str) -> str:
    """`ID STATE pN kind title [@owner]`; the title is flattened, bounded and quoted or withheld (untrusted_text)."""
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    state = folded.state.value if folded.state else "?"
    suffix = f" @{folded.owner}" if folded.owner else ""
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
        "state": folded.state.value if folded.state else None,
        "priority": item.priority,
        "labels": sorted(item.labels),
    }
    if hidden:
        row["text_findings"] = list(hidden)
    if folded.owner:
        row["owner"] = folded.owner
    if item.parent:
        row["parent"] = item.parent
    if item.needs:
        row["needs"] = list(item.needs)
    return row


def rows(snapshot: Snapshot, ids: List[str]) -> List[Dict[str, object]]:
    return [row_data(snapshot, item_id) for item_id in ids]
