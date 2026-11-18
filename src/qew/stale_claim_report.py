"""`qew check --stale-claims`: claimed items no one is visibly working.

`claimed` says nothing about a live worker when every agent claims as one shared identity, so an item is listed when it is claimed,
no open pull request covers it, no commit within the window cites it (`Refs: ID`), and neither it nor any descendant has a ledger
event within the window. Read-only: ledger, pull request records and the local commit log; no forge.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from qew import code_vcs, instants, ledger_graph, pr_record, vcs_refs
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_store import Snapshot

now = instants.now  # a name of this module so a test can set the clock


@dataclass(frozen=True)
class Row:
    item: str
    kind: str
    owner: str
    title: str
    last: str  # the latest ledger event of the item or a descendant
    hours: float


def _cited(config: Config, since: datetime) -> set[str]:
    """Ledger ids that commits since `since` cite in a `Refs:` trailer (all refs; a repo with no log yields none)."""
    done = code_vcs.run_git(config.root, "log", "--all", f"--since={since.isoformat()}", "--format=%B%x00")
    if done.returncode != 0:
        return set()
    return {item for message in done.stdout.split("\x00") for item in vcs_refs.cited_ids(message)}


def stale(config: Config, snapshot: Snapshot, hours: float) -> list[Row]:
    moment = now()
    window = moment - timedelta(hours=hours)
    kids = ledger_graph.children(snapshot)
    last: dict[str, datetime] = {}

    def latest(item_id: str) -> datetime:
        if item_id not in last:
            own = [t for t in (instants.parse_instant(e.at) for e in snapshot.events.get(item_id, [])) if t is not None]
            below = [latest(child) for child in kids.get(item_id, []) if child in snapshot.items]
            last[item_id] = max([*own, *below], default=datetime.min.replace(tzinfo=moment.tzinfo))
        return last[item_id]

    cited = _cited(config, window)
    found: list[Row] = []
    for item_id, folded in snapshot.folds.items():
        if folded.state != State.CLAIMED or item_id in cited or latest(item_id) >= window:
            continue
        if pr_record.covering_open(snapshot.ledger, item_id, drafts=True):
            continue
        item = snapshot.items[item_id]
        found.append(Row(item_id, item.kind.value, folded.owner or "", item.title, instants.format_instant(latest(item_id)),
                         round((moment - latest(item_id)).total_seconds() / 3600, 1)))
    return sorted(found, key=lambda row: -row.hours)
