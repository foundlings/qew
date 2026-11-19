"""The audit of pull requests on a schema 9 ledger: what `qew check` and `qew repair-landings` read.

A merged pull request's declared `closes` items must be completed, or flagged `land_pending` with the reason; a merged pull request
with no declaration cannot say what it did. A pending flag is acknowledged state (a note), an unrecorded one is a problem.
"""

from dataclasses import dataclass

from qew import pr_links, pr_record
from qew.ledger_store import Snapshot
from qew.pr_record import CLOSED_STATES


@dataclass(frozen=True)
class Unlanded:
    number: int
    key: str
    item: str
    why: str  # "" when nothing records why the item is open
    state: str


def _merged(snapshot: Snapshot, key: str, mirror_state: object) -> bool:
    return mirror_state == "merged" or "merged_seen" in pr_links.event_types(snapshot.ledger, key)


def unlanded(snapshot: Snapshot) -> list[Unlanded]:
    """Every `closes` item of a merged pull request that is not finished, with the recorded reason (or none)."""
    ledger = snapshot.ledger
    found: list[Unlanded] = []
    for _, mirror in pr_record.entries(ledger):
        key, number = mirror["_target"], mirror.get("number")
        if not _merged(snapshot, key, mirror.get("state")):
            continue
        held = pr_links.pending(ledger, key)
        for item_id in pr_links.load(ledger, key).items("closes"):
            folded = snapshot.folds.get(item_id)
            if folded is not None and folded.state not in CLOSED_STATES:
                found.append(Unlanded(number, key, item_id, held.get(item_id, ""), folded.state.value if folded.state else "unreadable"))
    return sorted(found, key=lambda row: (row.number, row.item))


def audit(snapshot: Snapshot) -> tuple[list[str], list[str]]:
    """(problems, notes) for a schema 9 ledger's pull requests."""
    ledger = snapshot.ledger
    problems: list[str] = []
    notes: list[str] = []
    for row in unlanded(snapshot):
        if row.why:
            notes.append(f"land_pending: pr #{row.number} closes {row.item} ({row.state}): {row.why}")
        else:
            problems.append(f"prs/{row.key}: pr #{row.number} merged but closes {row.item}, which is {row.state}, and nothing records why (fix: qew land {row.number})")
    closing: dict[str, list[int]] = {}
    for _, mirror in pr_record.entries(ledger):
        key, number = mirror["_target"], mirror["number"]
        declaration = pr_links.load(ledger, key)
        merged = _merged(snapshot, key, mirror.get("state"))
        if merged and not declaration.declared:
            problems.append(f"prs/{key}: pr #{number} merged with no link declaration, so nothing says what it completed (fix: qew pr migrate {number})")
        for item_id in declaration.roles:
            if item_id not in snapshot.items:
                problems.append(f"prs/{key}: pr #{number} links {item_id}, which is not an item")
        if not merged and mirror.get("state", "open") != "closed":
            for item_id in declaration.items("closes"):
                folded = snapshot.folds.get(item_id)
                if folded is None or folded.state not in CLOSED_STATES:  # a finished item has nothing left for two pull requests to both complete
                    closing.setdefault(item_id, []).append(number)
    notes.extend(f"open pull requests {' '.join('#' + str(n) for n in sorted(numbers))} all close {item_id}: merge them together or re-declare all but one"
                 for item_id, numbers in sorted(closing.items()) if len(numbers) > 1)
    return problems, notes
