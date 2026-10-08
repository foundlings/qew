"""`qew land`: after a pull request merged, record its head as evidence and complete the items it delivered."""

from typing import List, Optional

from qew import cmd_flow, ledger_config, pr_gate
from qew.cmd_pr import Build, Loaded, forge_errors, load
from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import actor
from qew.pr_record import positive_number


def register(commands) -> None:
    land = commands.add_parser("land", help="after a pull request merged, record its head as evidence and complete its claimed items")
    land.add_argument("number", type=int, help="pull request number")
    land.add_argument("--target", help="mirror target (slug or unique part)")


def run(arguments) -> bool:
    if arguments.command != "land":
        return False
    lines, failures = land(ledger_config.load(), arguments.number, arguments.target)
    print("\n".join(lines))
    if failures:
        raise QewError(f"{failures} item(s) were not completed; the lines above say why", 2)
    return True


def _order(loaded: Loaded) -> List[str]:
    """Children before their epics, so an epic completes after what it covers."""
    kinds = loaded.snapshot.items
    return sorted(loaded.record.items, key=lambda item_id: kinds[item_id].kind == Kind.EPIC)


def land(config, number: int, target: Optional[str], build: Optional[Build] = None):
    """Evidence and completion for every claimed item the merged pull request covered; (lines, failures)."""
    positive_number(number)
    loaded = load(config, number, target, build)
    with forge_errors(loaded.record.target):
        pull = loaded.forge.read_pull_request(number)
    if pull.state != "merged":
        raise QewError(f"pr #{number} is {pull.state}, not merged; merge it first with `qew pr merge {number}`", 2)
    lines: List[str] = []
    failures = 0
    for item_id in _order(loaded):
        state = loaded.states[item_id]
        owner = loaded.snapshot.folds[item_id].owner
        if state in (State.COMPLETED, State.CLOSED):
            lines.append(f"{item_id}: already {state.value}")
        elif state != State.CLAIMED:
            lines.append(f"{item_id}: {state.value}, unclaimed; not landed")
        elif owner != actor():
            lines.append(f"{item_id}: claimed by {owner}; not landed")
        else:
            failures += _land_one(config, loaded, item_id, pull.head_sha, lines)
    return lines, failures


def _land_one(config, loaded: Loaded, item_id: str, head_sha: str, lines: List[str]) -> int:
    snapshot = loaded.snapshot.ledger.snapshot()
    if snapshot.items[item_id].kind != Kind.EPIC and not pr_gate.evidence_covers(config, snapshot.folds[item_id].evidence, head_sha):
        cmd_flow.evidence(config, item_id, [head_sha], None)
    try:
        lines.append(cmd_flow.complete(config, item_id))
    except QewError as error:
        lines.append(f"{item_id}: not completed: {error}")
        return 1
    return 0
