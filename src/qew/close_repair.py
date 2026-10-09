"""Repair items an older pull closed as `not_planned` although the forge first closed them as completed.

The pull used to record every closed forge issue as `closed not_planned` and the next push rewrote the forge to match,
so the forge's current reason is no evidence. The first `closed` entry of the issue's event timeline is the human's.
"""

from dataclasses import dataclass
from collections.abc import Callable

from qew import ledger_config
from qew.forge import Forge, Unsupported
from qew.ledger_config import Config, Mirror
from qew.ledger_event import State
from qew.ledger_store import Ledger

REASON = "the forge's first close of this issue was completed; an older pull recorded it as not planned"


@dataclass(frozen=True)
class Repaired:
    item_id: str
    number: int


def candidates(ledger: Ledger) -> list[str]:
    """Closed items whose latest close is a not_planned the forge pull invented."""
    snapshot = ledger.snapshot()
    found = []
    for item_id, folded in snapshot.folds.items():
        if folded.state != State.CLOSED:
            continue
        closes = [e for e in snapshot.events[item_id] if e.type == "closed"]
        if closes and closes[-1].get("provenance") == "forge-import" and closes[-1].get("reason") == "not_planned":
            found.append(item_id)
    return sorted(found)


def repair(config: Config, ledger: Ledger, build: Callable[[Mirror], Forge], dry_run: bool) -> list[Repaired]:
    wanted = candidates(ledger)
    if not wanted:
        return []
    forges: dict[str, Forge] = {}
    for mirror in config.mirrors:
        forges[ledger_config.mirror_slug(mirror)] = build(mirror)
    repaired = []
    for item_id in wanted:
        for record in ledger.load_mirrors(item_id):
            forge: Forge | None = forges.get(record["_target"])
            if forge is None or record.get("kind") != "issue":
                continue
            try:
                history = forge.close_history(record["number"])
            except Unsupported:
                continue
            if not history or history[0] in ("not_planned", "duplicate"):
                continue
            if not dry_run:
                ledger.append_event(item_id, "reopened", reason=REASON, provenance="forge-import")
                ledger.append_event(item_id, "completed", provenance="forge-import")
            repaired.append(Repaired(item_id, record["number"]))
            break
    return repaired
