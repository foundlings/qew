"""What must be true of the covered items before a pull request may merge.

Pure over the ledger and the code repo: it never calls a forge.
"""

from collections.abc import Sequence

from qew import code_vcs, ledger_graph
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import Event, State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot
from qew.pr_record import CLOSED_STATES, PrRecord


def open_work(states: dict[str, State], record: PrRecord) -> list[str]:
    """The covered items that still have to be completed."""
    return [item_id for item_id in record.items if states[item_id] not in CLOSED_STATES]


def delivering(states: dict[str, State], record: PrRecord) -> list[str]:
    """The covered items someone is working on: only these can have delivered anything through the head."""
    return [item_id for item_id in record.items if states[item_id] == State.CLAIMED]


def open_children(snapshot: Snapshot, record: PrRecord, states: dict[str, State]) -> dict[str, list[str]]:
    """Claimed covered items with children that are neither finished nor covered by this same pull request."""
    covered = set(record.items)
    kids = ledger_graph.children(snapshot)
    found: dict[str, list[str]] = {}
    for item_id in delivering(states, record):
        blocking = [k for k in kids.get(item_id, []) if snapshot.folds[k].state not in CLOSED_STATES and k not in covered]
        if blocking:
            found[item_id] = blocking
    return found


def refuse_open_children(snapshot: Snapshot, record: PrRecord, states: dict[str, State]) -> None:
    found = open_children(snapshot, record, states)
    if found:
        shown = "; ".join(f"{item_id} has open children {' '.join(kids)}" for item_id, kids in found.items())
        raise QewError(f"pr #{record.number} cannot merge half-done work: {shown}", 2)


def evidence_covers(config: Config, events: Sequence[Event], head_sha: str) -> bool:
    """True when some recorded change is the pull request head or one of its ancestors."""
    kind = code_vcs.detect(config.root)
    for event in events:
        if event.get("vcs") != kind:
            continue
        for change in event.get("change_ids"):
            try:
                if code_vcs.is_ancestor(config.root, kind, change, head_sha):
                    return True
            except QewError:
                raise QewError(
                    f"cannot check evidence: head {head_sha[:12]} is not in this checkout; fetch it first", 4
                ) from None
    return False


def refuse_missing_evidence(config: Config, snapshot: Snapshot, record: PrRecord, states: dict[str, State], head_sha: str) -> None:
    missing = [
        item_id
        for item_id in delivering(states, record)
        if snapshot.items[item_id].kind != Kind.EPIC
        and not evidence_covers(config, snapshot.folds[item_id].evidence, head_sha)
    ]
    if missing:
        hints = "; ".join(f"qew evidence {item_id} --change CHANGE" for item_id in missing)
        raise QewError(
            f"pr #{record.number} head {head_sha[:12]} is not covered by recorded evidence of {' '.join(missing)} ({hints})", 2
        )
