"""`qew forge migrate --to TARGET`: move the primary forge to another configured target.

Restore the full ledger onto the target (issues, board, pull requests as links), verify that every selected item
has its issue there, and only then switch `[authority] primary` and demote the old primary in ONE config write.
Progress is derived from mirror files, so a failed run changes no config and a rerun resumes without duplicates.
"""

import json
from typing import Callable, List, Optional

from qew import config_edit, forge_registry, ledger_check, ledger_config, sync_plan
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards
from qew.errors import QewError
from qew.forge import Forge, ForgeError, Unsupported
from qew.ledger_config import Config, Mirror
from qew.restore import restore_target
from qew.sync import Target


def register(commands) -> None:
    forge = commands.add_parser("forge", help="forge authority: migrate")
    sub = forge.add_subparsers(dest="forge_command", required=True)
    migrate_ = sub.add_parser("migrate", help="restore the ledger onto another target, verify it, make it the primary")
    migrate_.add_argument("--to", required=True, help="configured mirror target (slug or unique part of it)")
    migrate_.add_argument("--remove-old", action="store_true", help="drop the old primary from the config instead of demoting it to read-only")


def run(arguments) -> bool:
    if arguments.command != "forge":
        return False
    print(migrate(ledger_config.load(), arguments.to, arguments.remove_old))
    return True


def _link_pull_requests(ledger, snapshot, target: Target) -> None:
    """A pull request of another target appears on the target's issue as a link comment, once."""
    for item_id in snapshot.items:
        records = ledger.load_mirrors(item_id)
        mine = next((r for r in records if r["_target"] == target.slug and r.get("kind") == "issue"), None)
        if mine is None:
            continue
        urls = [r["issue_url"] for r in records if r.get("kind") == "pr" and isinstance(r.get("issue_url"), str)]
        if not urls:
            continue
        known = " ".join(c.body for c in target.forge.list_comments(mine["number"]))
        for url in urls:
            if url not in known:
                target.forge.create_comment(mine["number"], f"Pull request: {url}")


def _verify(ledger, target: Target, selected: List[str]) -> int:
    remote = {issue.number for issue in target.forge.list_issues()}
    missing = []
    for item_id in selected:
        record = next((m for m in ledger.load_mirrors(item_id) if m["_target"] == target.slug and m.get("kind") == "issue"), None)
        if record is None or record["number"] not in remote:
            missing.append(item_id)
    if missing:
        raise QewError(f"migration to {target.slug} is incomplete: {len(missing)} of {len(selected)} items have no issue there "
                       f"({', '.join(missing[:5])}); the primary is unchanged, rerun to resume", 1)
    return len(selected)


def migrate(config: Config, wanted: str, remove_old: bool,
            build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    if not (config.root / ledger_config.CONFIG).is_file():
        raise QewError("the primary is set in the code repo's .qew/config.toml; run this from the code repo", 2)
    ledger = ledger_for(config)
    (chosen,) = choose(mirrors_with_boards(ledger, config), wanted)
    slug = ledger_config.mirror_slug(chosen)
    if slug == config.authority:
        return f"{slug} is already the primary"
    problems = ledger_check.check(ledger)
    if problems:
        raise QewError(f"the ledger has {len(problems)} problem(s); run `qew check`", 1)
    target = Target(chosen, slug, build(chosen))
    try:
        report = restore_target(ledger, target, config)
        if report.failures or report.stopped:
            raise QewError(f"migration to {slug} failed: {(report.failures or [report.stopped])[0]}; the primary is unchanged, rerun to resume", 1)
        snapshot = ledger.snapshot()
        _link_pull_requests(ledger, snapshot, target)
        count = _verify(ledger, target, sync_plan.selected(snapshot, chosen.select))
    except ForgeError as error:
        raise QewError(f"migration to {slug} failed: {error.detail}; the primary is unchanged, rerun to resume", 3) from None
    except Unsupported:
        count = _verify(ledger, target, sync_plan.selected(ledger.snapshot(), chosen.select))
    _switch(config, slug, remove_old)
    old = config.authority
    fate = "none before: the ledger remote arbitrated" if old == "none" else ("removed" if remove_old else "now read-only")
    return f"migrated to {slug}: verified {count} issues; old primary {old} {fate}"


def _switch(config: Config, slug: str, remove_old: bool) -> None:
    slugs = [ledger_config.mirror_slug(m) for m in config.mirrors]
    new_index = slugs.index(slug)
    old_index = slugs.index(config.authority) if config.authority != "none" else None

    def transform(text: str) -> str:
        text = config_edit.remove_key(text, "mirror", "read_only", new_index)
        text = config_edit.set_key(text, "authority", "primary", json.dumps(slug))
        if old_index is not None:
            text = (config_edit.remove_mirror(text, old_index) if remove_old
                    else config_edit.set_key(text, "mirror", "read_only", "true", old_index))
        return text

    config_edit.update(config.root, transform)
