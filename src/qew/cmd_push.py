"""`qew push`: send the ledger's pending changes to the forge mirrors, and nothing else.

The cheap half of `qew sync`: only the items with a pending write are sent, each issue is read back alone (no whole-board
listing, no pruning, no milestone pass). Forge-side edits to other items are not read; `qew sync --full` does that.
"""

import argparse
import re

from qew import claim_reconcile, forge_registry, ledger_config, replication
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.cmd_sync import choose, mirrors_with_boards, pending_by_target, sync_command

_PLAIN_HEAD = re.compile(r"\S+: created \d+ updated \d+ unchanged \d+")


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    push = commands.add_parser("push", help="send pending ledger changes to the forge mirrors: only the changed items, no whole-board read")
    push.add_argument("--target", help="only this target (slug or unique part of it)")
    push.add_argument("--item", action="append", help="only this item (repeatable)")
    push.add_argument("--dry-run", action="store_true", help="list the pending writes; reads and writes nothing on the forge")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "push":
        return False
    config = ledger_config.load()
    ledger = ledger_for(config)
    mirrors = choose(mirrors_with_boards(ledger, config), arguments.target)
    if not mirrors:
        print("no mirror targets configured")
        return True
    pending = pending_by_target(ledger, mirrors, arguments.item)
    if arguments.dry_run:
        rows = [f"{action} {item_id} {slug}" for slug, found in pending.items() for action, item_id in found if action != "baseline"]
        print("\n".join(rows) or "nothing pending")
        return True
    ids = list(dict.fromkeys(item_id for rows in pending.values() for action, item_id in rows if action != "baseline"))
    if not ids:
        settled = claim_reconcile.reconcile(config, ledger, forge_registry.build, None) if arguments.target is None else []
        print("\n".join(s.line() for s in settled) or "nothing pending")
        lost = claim_reconcile.lost_message(settled)
        if lost:
            raise QewError(lost, 3)
        return True
    output = sync_command(config, arguments.target, ids, False, tick=replication.checkpoint)
    print(f"pushed {len(ids)} item{'s' if len(ids) != 1 else ''} to {len(mirrors)} target{'s' if len(mirrors) != 1 else ''}")
    extra = [line for line in output.splitlines() if not _PLAIN_HEAD.fullmatch(line)]
    if extra:
        print("\n".join(extra))
    print("forge-side changes to other items were not read: `qew pull` fetches the ledger, `qew sync --full` reads the whole forge")
    return True
