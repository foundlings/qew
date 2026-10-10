"""Fill the evidence header of completed items from what their events recorded (the schema 4 migration).

For every completed item: the change ids its events cite, re-resolved against the configured target where the code
repository is at hand (an id that is known and did not land is dropped; one this checkout does not know is kept); when none remain, a typed absence built from how the
completion was recorded (no code, children, imported, or a bulk completion). Where the code repository is not available the
cited ids are kept as they are and `qew check` resolves them later.
"""

from dataclasses import dataclass

from qew import evidence_header
from qew.code_vcs import Landing
from qew.evidence_resolve import Resolver
from qew.ledger_event import Event, State
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot

BULK_REASON = "completed in bulk on a human's quote without delivery evidence"
UNDATED_REASON = "completed before evidence headers existed; no delivery evidence was recorded"


@dataclass(frozen=True)
class Filled:
    item_id: str
    evidence: tuple[str, ...]
    absent: str

    @property
    def kind(self) -> str:
        return "evidence" if self.evidence else self.absent.split(":", 1)[0]


def _cited(events: list[Event]) -> list[tuple[str, str]]:
    """(change id, vcs) in citation order, each once."""
    found: dict[str, str] = {}
    for event in events:
        if event.type == "evidence":
            for change in event.get("change_ids", []):
                found.setdefault(str(change), str(event.get("vcs", "")))
        elif event.type == "completed":
            for key in ("evidence", "delivered_by"):
                value = event.get(key)
                for change in value if isinstance(value, list) else []:
                    found.setdefault(str(change), "")
    return list(found.items())


def _absence(item_kind: Kind, completion: Event) -> str:
    shown = evidence_header.header_values(completion.data)
    if shown is not None and shown[1]:
        return shown[1]
    if item_kind == Kind.EPIC:
        return "children: all children completed"
    if completion.get("bulk_quote"):
        return f"historical: {evidence_header.one_line(str(completion.get('reason') or BULK_REASON))}"
    return f"historical: {UNDATED_REASON}"


def plan(snapshot: Snapshot, resolve: Resolver | None) -> list[Filled]:
    if resolve is not None:  # every cited id of every completed item in one batched lookup
        resolve.prepare(pair for item_id, item in snapshot.items.items() if snapshot.folds[item_id].state == State.COMPLETED
                        for pair in _cited(snapshot.events[item_id]))
    filled: list[Filled] = []
    for item_id, item in sorted(snapshot.items.items()):
        if snapshot.folds[item_id].state != State.COMPLETED or item.evidence or item.evidence_absent:
            continue
        events = snapshot.events[item_id]
        completions = [e for e in events if e.type == "completed"]
        if not completions:
            continue
        kept = [change for change, vcs in _cited(events) if resolve is None or resolve(change, vcs) is not Landing.NOT_ANCESTOR]  # unknown here: kept, `qew check` says so
        if kept:
            filled.append(Filled(item_id, tuple(kept), ""))
        else:
            filled.append(Filled(item_id, (), _absence(item.kind, completions[-1])))
    return filled


def apply(ledger: Ledger, filled: list[Filled]) -> None:
    from dataclasses import replace

    for each in filled:
        ledger.write_item(replace(ledger.load_item(each.item_id), evidence=each.evidence, evidence_absent=each.absent), overwrite=True)


def summary(filled: list[Filled]) -> str:
    counts: dict[str, int] = {}
    for each in filled:
        counts[each.kind] = counts.get(each.kind, 0) + 1
    return ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "none"
