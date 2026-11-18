"""`qew pr migrate`: a link declaration for every pull request recorded before schema 9, from what the ledger and the forge body say.

A ticked Progress box was delivered (closes), an unticked one was not (remains), an item the pull request dropped is related, and an
item recorded without a box is what the old landing would have completed (closes). Nothing is guessed upward: with the forge body
unreadable every recorded item stays open (remains), and a closes item that still has open descendants becomes remains. Migrating
never completes anything; a merged pull request's unfinished closes items are flagged `land_pending` for `qew land`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from qew import forge_http, forge_registry, ledger_config, pr_checklist, pr_links, pr_record
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.forge import Forge, ForgeError
from qew.ledger_config import Config
from qew.ledger_store import Ledger, Snapshot
from qew.mirror_types import MirrorRecord
from qew.pr_links import Role
from qew.pr_preflight_declared import _descendants
from qew.pr_record import CLOSED_STATES

Build = Callable[[ledger_config.Mirror], Forge]
DEFAULT_BUDGET = 300  # forge reads a run may make (open pull requests only)
RATE_FLOOR = 500  # the forge reports fewer calls left than this: stop, the merge gate needs them


@dataclass
class Row:
    number: int
    state: str  # "open" or "merged"
    key: str  # the record's mirror file stem
    roles: list[tuple[str, Role, str]] = field(default_factory=list)  # (item, role, why)
    no_item: str | None = None
    flags: list[str] = field(default_factory=list)


def _body(config: Config, ledger: Ledger, mirror: MirrorRecord, number: int, build: Build) -> tuple[str, str] | None:
    """The pull request's (body, state) from the forge, or None when it cannot be read (offline, deleted, no token)."""
    slug = mirror["_target"].rsplit("-pr-", 1)[0]
    try:
        from qew.cmd_pr import mirror_for  # imports this module's siblings for the loading pieces

        pull = build(mirror_for(config, ledger, slug)).read_pull_request(number)
        return pull.body, pull.state
    except (ForgeError, QewError):
        return None


def _row(snapshot: Snapshot, host: str, mirror: MirrorRecord, read: tuple[str, str] | None) -> Row:
    number = int(mirror["number"])
    body = read[0] if read is not None else None
    state = read[1] if read is not None else str(mirror.get("state", "open"))
    row = Row(number, "merged" if "merged" in (state, mirror.get("state")) else "open", mirror["_target"])  # either source saying merged is enough
    items = [i for i in mirror.get("items", [host]) if i in snapshot.items]
    dropped = [str(d).split(":", 1)[0] for d in mirror.get("dropped", [])]
    boxes = {b.address: b for b in pr_checklist.boxes(body)} if body is not None else {}
    roles: dict[str, tuple[Role, str]] = {}
    for item_id in [*items, *(a for a in boxes if a in snapshot.items and a not in items and a not in dropped)]:
        box = boxes.get(item_id)
        if body is None and snapshot.folds[item_id].state in CLOSED_STATES:
            roles[item_id] = ("closes", "already finished")
        elif body is None:
            roles[item_id] = ("remains", "box state unknown: kept open")
            flag = f"{item_id}: box state unknown (no forge body); declared remains, review it"
            if flag not in row.flags:
                row.flags.append(flag)
        elif box is None:
            roles[item_id] = ("closes", "recorded item")
        else:
            roles[item_id] = ("closes", "ticked box") if box.ticked else ("remains", "unticked box")
    for item_id in dropped:
        if item_id in snapshot.items and item_id not in roles:
            roles[item_id] = ("related", "dropped from the pull request")
    closing = {i for i, (role, _) in roles.items() if role == "closes"}
    for item_id in sorted(closing):
        if snapshot.folds[item_id].state in CLOSED_STATES:
            continue
        below = [d for d in _descendants(snapshot, item_id) if snapshot.folds[d].state not in CLOSED_STATES and d not in closing]
        if below:
            roles[item_id] = ("remains", f"parent-with-open-children: {' '.join(below[:6])}")
            row.flags.append(f"{item_id} stays open: parent-with-open-children {' '.join(below[:6])}")
    row.roles = [(item_id, role, why) for item_id, (role, why) in roles.items()]
    if not row.roles:
        row.no_item = "migrated: the record covered no ledger item"
    return row


@dataclass
class Plan:
    rows: list[Row]
    reads: int = 0
    stopped: str = ""  # why the forge reads ended early, "" when none did


def plan(config: Config, numbers: list[int], build: Build | None = None, offline: bool = False, budget: int = DEFAULT_BUDGET) -> Plan:
    """A row for every open or merged pull request without a declaration (all of them on a schema 8 ledger).

    Only a pull request the ledger records as open has its body read, one at a time, within `budget` reads and while the forge reports at
    least RATE_FLOOR calls left; a merged one never does (its items are landed, or pending, by ledger state), and `offline` reads none."""
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    wanted = [(host, m) for host, m in pr_record.entries(ledger)
              if m.get("state", "open") != "closed" and (not numbers or m.get("number") in numbers)
              and not (ledger.schema >= pr_links.LINKS_SCHEMA and pr_links.load(ledger, m["_target"]).declared)]
    use = build or forge_registry.build
    result = Plan([])
    for host, mirror in wanted:
        read: tuple[str, str] | None = None
        if mirror.get("state", "open") == "open" and not offline and not result.stopped:
            if result.reads >= budget:
                result.stopped = f"forge read budget of {budget} reached"
            elif forge_http.WATCH.remaining is not None and forge_http.WATCH.remaining < RATE_FLOOR:
                result.stopped = f"rate limit: the forge reports {forge_http.WATCH.remaining} calls left (floor {RATE_FLOOR})"
            else:
                result.reads += 1
                read = _body(config, ledger, mirror, int(mirror["number"]), use)
        result.rows.append(_row(snapshot, host, mirror, read))
    result.rows.sort(key=lambda row: (not row.flags, row.number))
    return result


def apply(config: Config, rows: list[Row]) -> int:
    """Write each row's declaration (and the pending flags of a merged pull request); returns how many pull requests were declared."""
    ledger = ledger_for(config)
    pr_links.require_schema(ledger)
    snapshot = ledger.snapshot()
    done = 0
    for row in rows:
        if pr_links.load(ledger, row.key).declared:
            continue
        if row.no_item is not None:
            pr_links.declare_no_item(ledger, row.key, row.no_item)
        else:
            pr_links.link(ledger, row.key, {item_id: role for item_id, role, _ in row.roles})
        if row.state == "merged":
            for item_id, role, _ in row.roles:
                if role == "closes" and snapshot.folds[item_id].state not in CLOSED_STATES:
                    pr_links.record_pending(ledger, row.key, item_id, f"migrated merged pull request: closes declared, item not completed; run `qew land {row.number}`")
        done += 1
    return done
