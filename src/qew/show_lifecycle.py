"""The lifecycle facts `qew show` adds to an item: who holds it, how it finished, what it links to."""

from typing import List, Tuple

from qew.ledger_store import Snapshot


def extras(snapshot: Snapshot, item_id: str) -> Tuple[dict, List[str]]:
    """(machine fields, text lines) for one item; the machine form always carries `claim`."""
    folded = snapshot.folds[item_id]
    data: dict = {"claim": folded.claim}
    lines: List[str] = []
    if folded.claim and folded.claim.get("lane"):
        parts = [f"lane {folded.claim['lane']}", f"kind {folded.claim['kind']}"]
        parts += [f"workspace {folded.claim['workspace']}"] if folded.claim.get("workspace") else []
        lines.append(f"claim: {', '.join(parts)}")
    return data, lines
