"""`qew sync`: push the ledger to every mirror target, each independently."""

import argparse
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta
from collections.abc import Callable, Sequence

from qew import trace, claim_reconcile, cmd_divergence, forge_registry, instants, label_prune, ledger_check, ledger_config, replication, sync_plan, syncworker
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.forge import Forge, ForgeError
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger
from qew.forge_pull import PullReport, pull_items, pull_target, wiped
from qew.sync import Target, TargetReport, sync_target


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sync = commands.add_parser("sync", help="push ledger state to every mirror target")
    sync.add_argument("--target", help="only this target (slug or unique part of it)")
    sync.add_argument("--item", action="append", help="only this item (repeatable): the cheap pass the background worker runs after writes")
    sync.add_argument("--full", action="store_true", help="re-read every issue from the forge, ignoring the pull cursor and cached pages")
    sync.add_argument("--dry-run", action="store_true", help="show what would change; reads the forge listing but writes nothing (with --prune-labels: deletes nothing)")
    sync.add_argument("--prune-labels", action="store_true",
                      help="instead of syncing, delete the labels qew retired from the target(s): prefixes converted to fields, and its own qew: kind: priority: status: author: labels it no longer emits (never a label that is merely unused)")


def run(arguments: argparse.Namespace) -> bool:
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


def _worker_round(config: Config, arguments: argparse.Namespace) -> bool:
    """One background pass; its output goes to the log. True when every target synced cleanly.

    The ids the writes queued are synced alone (a few forge calls each); with no queue, or after a failed pass left
    the catch-up marker, the whole target is synced.
    """
    queued = syncworker.take_queue(config)
    items = arguments.item or ([] if syncworker.catch_up_pending(config) else queued)
    try:
        print(sync_command(config, arguments.target, items or None, False, tick=replication.checkpoint, full=arguments.full))
        return True
    except QewError as error:
        print(f"qew: {error}")
        return False


def mirrors_with_boards(ledger: Ledger, config: Config) -> list[Mirror]:
    """Mirrors with the board `project ensure` recorded when the config names none."""
    mirrors = []
    for mirror in config.mirrors:
        board = ledger.read_board(ledger_config.mirror_slug(mirror))
        if mirror.project is None and board is not None:
            mirror = replace(mirror, project=board["id"])
        mirrors.append(mirror)
    return mirrors


def choose(mirrors: list[Mirror], wanted: str | None) -> list[Mirror]:
    if not wanted:
        return mirrors
    exact = [m for m in mirrors if ledger_config.mirror_slug(m) == wanted]
    found = exact or [m for m in mirrors if wanted in ledger_config.mirror_slug(m)]
    if not found:
        raise QewError(f"no mirror target matches `{wanted}`", 2)
    if len(found) > 1 and not exact:
        raise QewError(f"`{wanted}` matches several targets: {', '.join(ledger_config.mirror_slug(m) for m in found)}", 2)
    return found


def sync_command(config: Config, wanted: str | None, item: None | str | Sequence[str], dry_run: bool,
                 build: Callable[[Mirror], Forge] = forge_registry.build, budget_s: float | None = None,
                 tick: Callable[[], None] | None = None, full: bool = False,
                 clock: Callable[[], datetime] = instants.now) -> str:
    ledger = ledger_for(config)
    items = [item] if isinstance(item, str) else list(item or ())
    # Contradictions and unsafe actor names are reported by `qew check` but must never stop mirroring.
    with trace.phase("ledger check"):
        problems = [p for p in ledger_check.check(ledger, deep=False) if not p.startswith("contradiction:") and "actor is not a single token" not in p]
    if problems:
        raise QewError(f"the ledger has {len(problems)} problem(s); run `qew check`", 1)
    mirrors = choose(mirrors_with_boards(ledger, config), wanted)
    if not mirrors:
        if not dry_run and wanted is None:
            settled = claim_reconcile.reconcile(config, ledger, build, items[0] if len(items) == 1 else None)
            lost = claim_reconcile.lost_message(settled)
            if settled:
                output = "\n".join(s.line() for s in settled)
                if lost:
                    print(output)
                    raise QewError(lost, 3)
                return output
        return "no mirror targets configured"
    if dry_run:
        return plan(ledger, mirrors, items, build)
    lines = []
    failed = False
    for mirror in mirrors:
        target = Target(mirror, ledger_config.mirror_slug(mirror), build(mirror), ledger_config.is_read_only(config, mirror))
        # forge-side edits first, so the push cannot overwrite them; an item sync reads only those items' issues
        with trace.phase("pull"):
            pulled = (pull_items(ledger, target, _touched(ledger, items)) if items and not full
                      else pull_target(ledger, target, full=full, clock=clock))
        if config.replication.divergences == "ledger":
            with trace.phase("settle divergences"):
                if cmd_divergence.settle_automatically(config, target.slug):
                    ledger = ledger_for(config)  # the settling wrote events and mirror state: read them fresh
        if pulled.retry_after:  # rate limited while reading: pushing would only hit the same wall
            syncworker.set_backoff(pulled.retry_after)
            failed = True
            lines.append(f"{target.slug}: rate limited; retry after {_resume(pulled.retry_after, clock)}")
            continue
        with trace.phase("push (sync_target)"):
            report = sync_target(ledger, target, config, items or None,
                                 deadline=None if budget_s is None else time.monotonic() + budget_s, tick=tick)
        if report.retry_after:
            syncworker.set_backoff(report.retry_after)
        failed = failed or bool(report.failures)
        lines.extend(render(report, pulled))
    with trace.phase("claim reconcile"):
        settled = claim_reconcile.reconcile(config, ledger, build, items[0] if len(items) == 1 else None) if wanted is None else []
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


def _touched(ledger: Ledger, items: Sequence[str]) -> list[str]:
    """The named items and the ones their change moves (dependents, parent epics)."""
    snapshot = ledger.snapshot()
    chosen = [ledger.resolve(one, list(snapshot.items)) for one in items]
    return sorted({*chosen, *(i for one in chosen for i in sync_plan.moved_by(snapshot, one))})


def pending_by_target(ledger: Ledger, mirrors: list[Mirror], only: None | str | Sequence[str] = None) -> dict[str, list[tuple[str, str]]]:
    """slug -> [(action, item id)]: derived from event ids and mirror files, never queued."""
    snapshot = ledger.snapshot()
    wanted = [only] if isinstance(only, str) else list(only or ())
    chosen = {ledger.resolve(one, list(snapshot.items)) for one in wanted}
    result = {}
    for mirror in mirrors:
        slug = ledger_config.mirror_slug(mirror)
        rows = []
        for item_id in sync_plan.topological(snapshot, sync_plan.selected(snapshot, mirror.select)):
            if chosen and item_id not in chosen:
                continue
            record = next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug), None)
            events = snapshot.events[item_id]
            if record is None:
                rows.append(("create", item_id))
            elif events and record.get("synced_event") != events[-1].id:
                rows.append(("update", item_id))
        result[slug] = rows
    return result


def plan(ledger: Ledger, mirrors: list[Mirror], item: None | str | Sequence[str],
         build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    """The pending writes, read from the ledger; the forge listing is read too, to see a wiped repository."""
    pending = pending_by_target(ledger, mirrors, item)
    lines = []
    for mirror in mirrors:
        slug = ledger_config.mirror_slug(mirror)
        wiped_ids, note = _wiped_items(ledger, build, mirror, slug)
        if note:
            lines.append(note)
        rows = [("create", i) for i in wiped_ids] or pending[slug]
        lines.extend(f"{action} {item_id} {slug}" for action, item_id in rows)
    return "\n".join(lines) or "nothing to sync"


def _wiped_items(ledger: Ledger, build: Callable[[Mirror], Forge], mirror: Mirror, slug: str) -> tuple[list[str], str]:
    """(item ids to recreate, note): the items whose mirrored issues are all gone from the forge listing."""
    mirrored = {r["number"]: item_id for item_id in ledger.item_ids() for r in ledger.load_mirrors(item_id)
                if r["_target"] == slug and r.get("kind") == "issue"}
    try:
        remote = {issue.number for issue in build(mirror).list_issues()}
    except (ForgeError, QewError) as error:
        return [], f"{slug}: forge not read ({getattr(error, 'detail', error)}); plan from the ledger only"
    return (sorted(set(mirrored.values())), "") if wiped(mirrored, remote) else ([], "")


def _resume(seconds: float, clock: Callable[[], datetime] = instants.now) -> str:
    """`300s (about 21:35 UTC)`: how long to wait and the UTC clock time a rerun is likely to work."""
    return f"{int(seconds)}s (about {(clock() + timedelta(seconds=seconds)).strftime('%H:%M')} UTC)"


def render(report: TargetReport, pulled: PullReport | None = None, clock: Callable[[], datetime] = instants.now) -> list[str]:
    head = f"{report.slug}: created {report.created} updated {report.updated} unchanged {report.unchanged}"
    if report.adopted:
        head += f" adopted {report.adopted}"
    if report.skipped:
        head += f" skipped {','.join(report.skipped)}"
    if pulled is not None and (pulled.applied or pulled.divergences or pulled.new_items or pulled.comments or pulled.duplicates
                               or pulled.recreated):
        head += (f" pulled applied={pulled.applied} divergences={pulled.divergences}"
                 f" new={pulled.new_items} comments={pulled.comments} duplicates_closed={pulled.duplicates}")
        if pulled.recreated:
            head += f" recreated={pulled.recreated}"
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
        lines.append(f"  retry after {_resume(report.retry_after, clock)}")
    return lines
