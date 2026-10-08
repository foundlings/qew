"""Post-tool completion hint after the agent ran `qew pr merge N`.

Read-only: it never completes or takes over anything. It speaks only when the
forge says the pull request is merged; any failure to find out is silence.
"""

from pathlib import Path
from typing import Callable, List, Optional

from qew import forge_registry, hook_guard, ledger_config
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards
from qew.errors import QewError
from qew.forge import ForgeError
from qew.ledger_event import State
from qew.ledger_store import actor

Build = Callable


def reminder(command: str, cwd: Optional[Path], build: Build = forge_registry.build) -> Optional[str]:
    numbers = hook_guard.qew_pr_merges(command)
    if not numbers or ledger_config.find_root(cwd) is None:
        return None
    config = ledger_config.load(cwd)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    lines: List[str] = []
    for number in numbers:
        for item_id, record in _recorded(ledger, number):
            if snapshot.folds[item_id].state != State.CLAIMED or not _merged(config, ledger, record, number, build):
                continue
            folded = snapshot.folds[item_id]
            if folded.owner != actor():
                lines.append(
                    f"qew: pr #{number} is merged; {item_id} is claimed by {folded.owner}, "
                    f"taking it over is `qew start {item_id} --take-over --reason TEXT`."
                )
            elif folded.evidence:
                lines.append(f"qew: pr #{number} is merged; {item_id}: evidence recorded; `qew complete {item_id}`, or `qew land {number}` for all.")
            else:
                lines.append(
                    f"qew: pr #{number} is merged; record `qew evidence {item_id}`, then `qew complete {item_id}` (or `qew land {number}`)."
                )
    return "\n".join(lines) or None


def _recorded(ledger, number: int):
    for item_id in ledger.item_ids():
        for record in ledger.load_mirrors(item_id):
            if record.get("kind") == "pr" and record.get("number") == number:
                yield item_id, record


def _merged(config, ledger, record: dict, number: int, build: Build) -> bool:
    slug = record["_target"].rsplit("-pr-", 1)[0]
    try:
        mirror = next(m for m in mirrors_with_boards(ledger, config) if ledger_config.mirror_slug(m) == slug)
        return build(mirror).read_pull_request(number).state == "merged"
    except (StopIteration, ForgeError, QewError, OSError):
        return False
