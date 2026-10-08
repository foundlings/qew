"""`qew sync`: push the ledger to every mirror target, each independently."""

import os
import time
from dataclasses import replace
from datetime import datetime
from typing import Callable, List, Optional

from qew import claim_reconcile, forge_registry, instants, label_prune, ledger_check, ledger_config, replication, sync_plan, syncworker
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.forge import Forge
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger
from qew.forge_pull import PullReport, pull_target
from qew.sync import Target, TargetReport, sync_target


def register(commands) -> None:
    sync = commands.add_parser("sync", help="push ledger state to every mirror target")
    sync.add_argument("--target", help="only this target (slug or unique part of it)")
    sync.add_argument("--item", help="only this item")
    sync.add_argument("--full", action="store_true", help="re-read every issue from the forge, ignoring the pull cursor and cached pages")
    sync.add_argument("--dry-run", action="store_true", help="show what would change without calling any forge (with --prune-labels: reads the forge, deletes nothing)")
    sync.add_argument("--prune-labels", action="store_true",
                      help="instead of syncing, delete the unused qew: kind: priority: status: author: labels from the target(s)")


def run(arguments) -> bool:
    if arguments.command != "sync":
        return False
    config = ledger_config.load()
    if arguments.prune_labels:
        if arguments.item or arguments.full:
            raise QewError("--prune-labels works on whole targets; it takes neither --item nor --full", 2)
        mirrors = choose(mirrors_with_boards(ledger_for(config), config), arguments.target)
        print(label_prune.prune(ledger_for(config), config, mirrors, arguments.dry_run) if mirrors else "no mirror targets configured")
        return True
    if os.environ.get("QEW_BACKGROUND") and not arguments.dry_run:
        syncworker.run_worker(config, lambda: _worker_round(config, arguments))
        return True
    print(sync_command(config, arguments.target, arguments.item, arguments.dry_run, tick=replication.checkpoint,
                        full=arguments.full))
    return True


def _worker_round(config: Config, arguments) -> bool:
    """One background pass; its output goes to the log. True when every target synced cleanly."""
    try:
        print(sync_command(config, arguments.target, arguments.item, False, tick=replication.checkpoint, full=arguments.full))
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
                 tick: Optional[Callable[[], None]] = None, full: bool = False,
                 clock: Callable[[], datetime] = instants.now) -> str:
    ledger = ledger_for(config)
    # Contradictions and unsafe actor names are reported by `qew check` but must never stop mirroring.
    problems = [p for p in ledger_check.check(ledger) if not p.startswith("contradiction:") and "actor is not a single token" not in p]
    if problems:
        raise QewError(f"the ledger has {len(problems)} problem(s); run `qew check`", 1)
    mirrors = choose(mirrors_with_boards(ledger, config), wanted)
    if not mirrors:
        if not dry_run and wanted is None:
            settled = claim_reconcile.reconcile(config, ledger, build, item)
            lost = claim_reconcile.lost_message(settled)
            if settled:
                output = "\n".join(s.line() for s in settled)
                if lost:
                    print(output)
                    raise QewError(lost, 3)
                return output
        return "no mirror targets configured"
    if dry_run:
        return plan(ledger, mirrors, item)
    lines = []
    failed = False
    for mirror in mirrors:
        target = Target(mirror, ledger_config.mirror_slug(mirror), build(mirror), ledger_config.is_read_only(config, mirror))
        pulled = pull_target(ledger, target, full=full, clock=clock)  # forge-side edits first, so the push cannot overwrite them
        if pulled.retry_after:  # rate limited while reading: pushing would only hit the same wall
            syncworker.set_backoff(pulled.retry_after)
            failed = True
            lines.append(f"{target.slug}: rate limited; retry after {int(pulled.retry_after)}s")
            continue
        report = sync_target(ledger, target, config, item,
                             deadline=None if budget_s is None else time.monotonic() + budget_s, tick=tick)
        if report.retry_after:
            syncworker.set_backoff(report.retry_after)
        failed = failed or bool(report.failures)
        lines.extend(render(report, pulled))
    settled = claim_reconcile.reconcile(config, ledger, build, item) if wanted is None else []
    lines.extend(s.line() for s in settled)
    output = "\n".join(lines)
    if failed:
        print(output)
        raise QewError("sync finished with failures; fix the cause above, then run `qew sync` to catch the mirrors up", 1)
    lost = claim_reconcile.lost_message(settled)
    if lost:
        print(output)
        raise QewError(lost, 3)
    return output


def pending_by_target(ledger: Ledger, mirrors: List[Mirror], only: Optional[str] = None) -> dict:
    """slug -> [(action, item id)]: derived from event ids and mirror files, never queued."""
    snapshot = ledger.snapshot()
    chosen = ledger.resolve(only, list(snapshot.items)) if only else None
    result = {}
    for mirror in mirrors:
        slug = ledger_config.mirror_slug(mirror)
        rows = []
        for item_id in sync_plan.topological(snapshot, sync_plan.selected(snapshot, mirror.select)):
            if chosen and item_id != chosen:
                continue
            record = next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug), None)
            events = snapshot.events[item_id]
            if record is None:
                rows.append(("create", item_id))
            elif events and record.get("synced_event") != events[-1].id:
                rows.append(("update", item_id))
        result[slug] = rows
    return result


def plan(ledger: Ledger, mirrors: List[Mirror], item: Optional[str]) -> str:
    lines = [f"{action} {item_id} {slug}" for slug, rows in pending_by_target(ledger, mirrors, item).items()
             for action, item_id in rows]
    return "\n".join(lines) or "nothing to sync"


def render(report: TargetReport, pulled: Optional[PullReport] = None) -> List[str]:
    head = f"{report.slug}: created {report.created} updated {report.updated} unchanged {report.unchanged}"
    if report.adopted:
        head += f" adopted {report.adopted}"
    if report.skipped:
        head += f" skipped {','.join(report.skipped)}"
    if pulled is not None and (pulled.applied or pulled.divergences or pulled.new_items or pulled.comments or pulled.duplicates):
        head += (f" pulled applied={pulled.applied} divergences={pulled.divergences}"
                 f" new={pulled.new_items} comments={pulled.comments} duplicates_closed={pulled.duplicates}")
    if report.pruned:
        head += f" pruned {report.pruned}"
    if report.milestones_created or report.milestones_updated or report.milestones_unchanged:
        head += (f" milestones created {report.milestones_created} updated {report.milestones_updated}"
                 f" unchanged {report.milestones_unchanged}")
    lines = [head]
    lines.extend(f"  warning {warning}" for warning in report.warnings)
    lines.extend(f"  failed {failure}" for failure in report.failures)
    if pulled is not None:
        lines.extend(f"  pull failed {failure}" for failure in pulled.failures)
    if report.stopped:
        lines.append(f"  stopped: {report.stopped}")
    if report.retry_after:
        lines.append(f"  retry after {int(report.retry_after)}s")
    return lines
