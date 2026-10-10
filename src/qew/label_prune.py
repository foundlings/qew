"""Delete the label definitions that nothing carries and the ledger no longer wants: qew's own namespaces, or with
`everything` any label (a replaced field label, a forge default); the gate labels qew itself applies always stay."""

from collections.abc import Callable

from qew import forge_registry, label_style, ledger_config, ledger_graph, sync_plan
from qew.errors import QewError
from qew.forge import Forge
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger


KEPT = frozenset({ledger_graph.GATED_LABEL, ledger_graph.APPROVAL_LABEL})  # applied by qew itself whenever an item needs them


def unused_managed(ledger: Ledger, mirror: Mirror, forge: Forge, everything: bool = False) -> list[str]:
    """Labels defined on the forge that no issue or pull request carries and no item will apply: managed-prefix ones, or all."""
    snapshot = ledger.snapshot()
    overlay = mirror.mode == "overlay"
    wanted = {label for item_id in sync_plan.selected(snapshot, mirror.select)
              for label in sync_plan.labels(snapshot, snapshot.items[item_id], overlay)}
    in_use = forge.labels_in_use()
    return [name for name in forge.list_repo_labels()
            if (everything and name not in KEPT or label_style.is_managed(name)) and name not in in_use and name not in wanted]


def prune(ledger: Ledger, config: Config, mirrors: list[Mirror], dry_run: bool, everything: bool = False,
          build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    """Refuses before any call when a chosen target is read-only; then lists (dry run) or deletes per target."""
    slugs = [ledger_config.mirror_slug(m) for m in mirrors]
    read_only = [s for m, s in zip(mirrors, slugs) if ledger_config.is_read_only(config, m)]
    if read_only:
        raise QewError(f"{', '.join(read_only)}: read-only target, labels are never pruned there; name a writable one with --target", 2)
    lines: list[str] = []
    for mirror, slug in zip(mirrors, slugs):
        forge = build(mirror)
        names = unused_managed(ledger, mirror, forge, everything)
        if not names:
            lines.append(f"{slug}: no unused {'' if everything else 'managed '}labels")
        for name in names:
            if not dry_run:
                forge.delete_label(name)
            lines.append(f"{slug}: {'would delete' if dry_run else 'deleted'} label {name}")
    return "\n".join(lines)

