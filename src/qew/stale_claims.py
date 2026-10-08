"""Claims whose pull request already merged: reported with the fix, never changed."""

from typing import List

from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_store import Snapshot


def merged_pull_requests(snapshot: Snapshot, item_id: str) -> List[int]:
    """Numbers of the item's recorded pull requests whose recorded state is merged, while the item is claimed.

    An unreadable mirror record yields nothing here; `qew check` reports the record itself.
    """
    if snapshot.folds[item_id].state != State.CLAIMED:
        return []
    try:
        records = snapshot.ledger.load_mirrors(item_id)
    except QewError:
        return []
    numbers = [r["number"] for r in records if r.get("kind") == "pr" and r.get("state") == "merged" and type(r.get("number")) is int]
    return sorted(set(numbers))
