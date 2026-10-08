"""`qew sync`: push the ledger to every mirror target, each independently."""

import os
import time
from dataclasses import replace
from typing import Callable, List, Optional

from qew import forge_registry, ledger_check, ledger_config, replication, sync_plan, syncworker
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.forge import Forge
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger
from qew.sync import Target, TargetReport, sync_target


def register(commands) -> None:
    sync = commands.add_parser("sync", help="push ledger state to every mirror target")
    sync.add_argument("--target", help="only this target (slug or unique part of it)")
    sync.add_argument("--item", help="only this item")
    sync.add_argument("--dry-run", action="store_true", help="show what would change without calling any forge")


def run(arguments) -> bool:
    if arguments.command != "sync":
        return False
    config = ledger_config.load()
    if os.environ.get("QEW_BACKGROUND") and not arguments.dry_run:
        syncworker.run_worker(config, lambda: _worker_round(config, arguments))
        return True
    print(sync_command(config, arguments.target, arguments.item, arguments.dry_run, tick=replication.checkpoint))
    return True


def _worker_round(config: Config, arguments) -> bool:
    """One background pass; its output goes to the log. True when every target synced cleanly."""
    try:
        print(sync_command(config, arguments.target, arguments.item, False, tick=replication.checkpoint))
        return True
    except QewError as error:
        print(f"qew: {error}")
        return False


def mirrors_with_boards(ledger: Ledger, config: Config) -> List[Mirror]:
    """Mirrors with the board `project ensure` recorded when the config names none."""
    mirrors = []
    for mirror in config.mirrors:
        board = ledger.read_board(ledger_config.mirror_slug(mirror))
        if mirror.project is None and board is not None:
            mirror = replace(mirror, project=board["id"])
        mirrors.append(mirror)
    return mirrors


def choose(mirrors: List[Mirror], wanted: Optional[str]) -> List[Mirror]:
    if not wanted:
        return mirrors
    exact = [m for m in mirrors if ledger_config.mirror_slug(m) == wanted]
    found = exact or [m for m in mirrors if wanted in ledger_config.mirror_slug(m)]
    if not found:
        raise QewError(f"no mirror target matches `{wanted}`", 2)
    if len(found) > 1 and not exact:
        raise QewError(f"`{wanted}` matches several targets: {', '.join(ledger_config.mirror_slug(m) for m in found)}", 2)
    return found


def sync_command(config: Config, wanted: Optional[str], item: Optional[str], dry_run: bool,
                 build: Callable[[Mirror], Forge] = forge_registry.build, budget_s: Optional[float] = None,
                 tick: Optional[Callable[[], None]] = None) -> str:
    ledger = ledger_for(config)
    problems = ledger_check.check(ledger)
    if problems:
        raise QewError(f"the ledger has {len(problems)} problem(s); run `qew check`", 1)
    mirrors = choose(mirrors_with_boards(ledger, config), wanted)
    if not mirrors:
        return "no mirror targets configured"
    if dry_run:
        return plan(ledger, mirrors, item)
    lines = []
    failed = False
    for mirror in mirrors:
        report = sync_target(ledger, Target(mirror, ledger_config.mirror_slug(mirror), build(mirror)), config, item,
                             deadline=None if budget_s is None else time.monotonic() + budget_s, tick=tick)
        failed = failed or bool(report.failures)
        lines.extend(render(report))
    output = "\n".join(lines)
    if failed:
        print(output)
        raise QewError("sync finished with failures", 1)
    return output


def plan(ledger: Ledger, mirrors: List[Mirror], item: Optional[str]) -> str:
    snapshot = ledger.snapshot()
    only = ledger.resolve(item, list(snapshot.items)) if item else None
    lines = []
    for mirror in mirrors:
        slug = ledger_config.mirror_slug(mirror)
        for item_id in sync_plan.topological(snapshot, sync_plan.selected(snapshot, mirror.select)):
            if only and item_id != only:
                continue
            record = next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug), None)
            events = snapshot.events[item_id]
            if record is None:
                lines.append(f"create {snapshot.short(item_id)} {slug}")
            elif events and record.get("synced_event") != events[-1].id:
                lines.append(f"update {snapshot.short(item_id)} {slug}")
    return "\n".join(lines) or "nothing to sync"


def render(report: TargetReport) -> List[str]:
    head = f"{report.slug}: created {report.created} updated {report.updated} unchanged {report.unchanged}"
    if report.adopted:
        head += f" adopted {report.adopted}"
    if report.skipped:
        head += f" skipped {','.join(report.skipped)}"
    lines = [head]
    lines.extend(f"  failed {failure}" for failure in report.failures)
    if report.stopped:
        lines.append(f"  stopped: {report.stopped}")
    return lines
