"""`qew land`: after a pull request merged, record its head as evidence and complete the items it delivered."""

import argparse
from dataclasses import dataclass, field
from typing import cast

from qew import cmd_claim, cmd_finish, code_vcs, ledger_config, outcome, pr_checklist, pr_gate
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
    land.add_argument("--integrator", action="store_true", help="land the items another actor claimed too, as the one who merged (needs --reason)")
    land.add_argument("--reason", help="with --integrator: why the merger lands them, recorded on each item")
    land.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "land":
        return False
    result = land(ledger_config.load(), arguments.number, arguments.target, integrator=arguments.integrator, reason=arguments.reason)
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


def _order(loaded: Loaded, ids: list[str]) -> list[str]:
    """Children before their epics, so an epic completes after what it covers."""
    kinds = loaded.snapshot.items
    return sorted(ids, key=lambda item_id: kinds[item_id].kind == Kind.EPIC)


def _boxed(loaded: Loaded, body: str) -> list[str]:
    """The items the merged body's Progress boxes name that the record does not cover: the record froze at open, and a merged
    pull request can no longer have its covered items synced."""
    named = [box.address for box in pr_checklist.boxes(body) if box.address in loaded.snapshot.items]
    return [item_id for item_id in dict.fromkeys(named) if item_id not in loaded.record.items]


def land(config: Config, number: int, target: str | None, build: Build | None = None, integrator: bool = False,
         reason: str | None = None) -> Landing:
    """Evidence and completion for every claimed item the merged pull request covered: the recorded ones, and the ones its
    Progress boxes name (completed only when their evidence is on the merged head, since the head is not proof for them).

    `integrator`: the caller merged it and lands the items another actor holds too, on the take-over path with `reason`."""
    positive_number(number)
    if integrator and not (reason or "").strip():
        raise QewError("--integrator needs --reason TEXT: say why the merger lands items another actor claimed", 2)
    if reason is not None and not integrator:
        raise QewError("--reason is only for --integrator", 2)
    loaded = load(config, number, target, build, owned=False)  # the holder is checked per item below, not per pull request
    with forge_errors(loaded.record.target):
        pull = loaded.forge.read_pull_request(number)
    if pull.state != "merged":
        raise QewError(f"pr #{number} is {pull.state}, not merged; merge it first with `qew pr merge {number}`", 2)
    result = Landing()
    if not code_vcs.valid_change(code_vcs.detect(config.root), pull.head_sha):
        raise QewError(f"indeterminate: pr #{number} merged at `{pull.head_sha}`, which is not a commit id; nothing was written", 4)
    boxed = []
    states = dict(loaded.states)
    for item_id in _boxed(loaded, pull.body):
        folded = loaded.snapshot.folds[item_id]
        if folded.errors or folded.state is None or any(e.type == "claimed" for e in folded.divergences):
            result.lines.append(f"{item_id}: named by a Progress box in pr #{number}, but its history cannot be read or its owner is contested; not landed (run `qew check`)")
            result.open.append(item_id)
            continue
        states[item_id] = folded.state
        boxed.append(item_id)
    for item_id in _order(loaded, [*loaded.record.items, *boxed]):
        state = states[item_id]
        owner = loaded.snapshot.folds[item_id].owner
        mine = owner == actor() or cmd_claim.holds_ancestor(loaded.snapshot, item_id, actor())
        if state in (State.COMPLETED, State.CLOSED):
            result.lines.append(f"{item_id}: already {state.value}")
            result.clean.append(item_id)
        elif state != State.CLAIMED:
            result.lines.append(f"{item_id}: {state.value}, unclaimed; not landed")
            result.open.append(item_id)
        elif not mine and not integrator:
            result.lines.append(f"{item_id}: claimed by {owner}; not landed (to finish it anyway: qew complete {item_id} --take-over --reason TEXT, or qew land {number} --integrator --reason TEXT if you merged it)")
            result.held.append({"id": item_id, "holder": owner})
        elif pull.base_branch != config.target:
            result.lines.append(f"{item_id}: still open: merged into {pull.base_branch}, not the target {config.target}")
            result.open.append(item_id)
        elif item_id in boxed and loaded.snapshot.items[item_id].kind != Kind.EPIC and not pr_gate.evidence_covers(
                config, loaded.snapshot.folds[item_id].evidence, pull.head_sha):
            result.lines.append(f"{item_id}: has a Progress box in pr #{number} but no evidence on its merged head; not landed "
                                f"(record it with `qew evidence {item_id} --change CHANGE`, then `qew land {number}` again)")
            result.open.append(item_id)
        else:
            takeover = f"integrator landed pr #{number} (held by {owner}): {reason}" if not mine and integrator else None
            result.failures += _land_one(config, loaded, item_id, pull.head_sha, result, takeover)
    if not result.landed and not result.failures:
        everything_done = bool(result.clean) and len(result.clean) == len(loaded.record.items) + len(boxed)
        result.lines.append("nothing to land: all covered items complete" if everything_done else "nothing to land")
    return result


def _land_one(config: Config, loaded: Loaded, item_id: str, head_sha: str, result: Landing, takeover: str | None = None) -> int:
    snapshot = loaded.snapshot.ledger.snapshot()
    holder = snapshot.folds[item_id].owner
    if snapshot.folds[item_id].state == State.COMPLETED:  # an epic finishes by itself with its last child
        result.lines.append(f"{item_id}: completed with its children")
        result.landed.append(item_id)
        return 0
    if snapshot.items[item_id].kind != Kind.EPIC and not pr_gate.evidence_covers(config, snapshot.folds[item_id].evidence, head_sha):
        cmd_finish.evidence(config, item_id, [head_sha], None, holder if takeover else None)
    try:
        result.lines.append(cast(str, cmd_finish.complete(config, item_id, takeover, takeover is not None)["line"]))
        result.landed.append(item_id)
    except QewError as error:
        result.lines.append(f"{item_id}: not completed: {error}")
        return 1
    return 0
