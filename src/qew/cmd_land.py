"""`qew land`: after a pull request merged, record its head as evidence and complete the items it delivered."""

import argparse
from dataclasses import dataclass, field
from typing import cast

from qew import cmd_finish, code_vcs, ledger_config, outcome, pr_gate
from qew.cmd_pr import Build, Loaded, forge_errors, load
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import actor
from qew.pr_record import positive_number


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    land = commands.add_parser("land", help="after a pull request merged, record its head as evidence and complete its claimed items")
    land.add_argument("number", type=int, help="pull request number")
    land.add_argument("--target", help="mirror target (slug or unique part)")
    land.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "land":
        return False
    result = land(ledger_config.load(), arguments.number, arguments.target)
    if arguments.json:
        print(outcome.render(arguments, "", state=result.state, landed=result.landed, held=result.held,
                             clean=result.clean, open=result.open))
    else:
        print("\n".join(result.lines))
    if result.failures:
        raise QewError(f"{result.failures} item(s) were not completed; the lines above say why", 2)
    return True


@dataclass
class Landing:
    """What one `qew land` did: the report lines and the same facts as lists for a caller to parse."""

    state: str = "merged"
    lines: list[str] = field(default_factory=list)
    failures: int = 0
    landed: list[str] = field(default_factory=list)
    held: list[JsonObject] = field(default_factory=list)
    clean: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)


def _order(loaded: Loaded) -> list[str]:
    """Children before their epics, so an epic completes after what it covers."""
    kinds = loaded.snapshot.items
    return sorted(loaded.record.items, key=lambda item_id: kinds[item_id].kind == Kind.EPIC)


def land(config: Config, number: int, target: str | None, build: Build | None = None) -> Landing:
    """Evidence and completion for every claimed item the merged pull request covered."""
    positive_number(number)
    loaded = load(config, number, target, build)
    with forge_errors(loaded.record.target):
        pull = loaded.forge.read_pull_request(number)
    if pull.state != "merged":
        raise QewError(f"pr #{number} is {pull.state}, not merged; merge it first with `qew pr merge {number}`", 2)
    result = Landing()
    if not code_vcs.valid_change(code_vcs.detect(config.root), pull.head_sha):
        raise QewError(f"indeterminate: pr #{number} merged at `{pull.head_sha}`, which is not a commit id; nothing was written", 4)
    for item_id in _order(loaded):
        state = loaded.states[item_id]
        owner = loaded.snapshot.folds[item_id].owner
        if state in (State.COMPLETED, State.CLOSED):
            result.lines.append(f"{item_id}: already {state.value}")
            result.clean.append(item_id)
        elif state != State.CLAIMED:
            result.lines.append(f"{item_id}: {state.value}, unclaimed; not landed")
            result.open.append(item_id)
        elif owner != actor():
            result.lines.append(f"{item_id}: claimed by {owner}; not landed (to finish it anyway: qew complete {item_id} --take-over --reason TEXT)")
            result.held.append({"id": item_id, "holder": owner})
        elif pull.base_branch != config.target:
            result.lines.append(f"{item_id}: still open: merged into {pull.base_branch}, not the target {config.target}")
            result.open.append(item_id)
        else:
            result.failures += _land_one(config, loaded, item_id, pull.head_sha, result)
    if not result.landed and not result.failures:
        result.lines.append("nothing to land")
    return result


def _land_one(config: Config, loaded: Loaded, item_id: str, head_sha: str, result: Landing) -> int:
    snapshot = loaded.snapshot.ledger.snapshot()
    if snapshot.folds[item_id].state == State.COMPLETED:  # an epic finishes by itself with its last child
        result.lines.append(f"{item_id}: completed with its children")
        result.landed.append(item_id)
        return 0
    if snapshot.items[item_id].kind != Kind.EPIC and not pr_gate.evidence_covers(config, snapshot.folds[item_id].evidence, head_sha):
        cmd_finish.evidence(config, item_id, [head_sha], None)
    try:
        result.lines.append(cast(str, cmd_finish.complete(config, item_id)["line"]))
        result.landed.append(item_id)
    except QewError as error:
        result.lines.append(f"{item_id}: not completed: {error}")
        return 1
    return 0
