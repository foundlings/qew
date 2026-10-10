"""Delete the label definitions qew itself retired, and only those.

Retired is narrow on purpose: a label `PREFIX:VALUE` whose prefix became a field (`phase:12` once `phase` is a field), and a label in
one of qew's own namespaces (`qew:` `kind:` `priority:` `status:` `author:`) that no item produces any more (`author:old`). A label
that merely nothing carries is never deleted: forge defaults, a team's labels and labels made in preparation stay, and so do the
labels qew keeps emitting (`epic` `task` `P0`..`P4` `ready` `in-progress` `gated` `needs:approval`). A label an issue or pull
request still carries is never deleted either, retired or not.
"""

from collections.abc import Callable

from qew import forge_registry, label_style, ledger_config, ledger_graph, sync_plan
from qew.errors import QewError
from qew.forge import Forge
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger, Snapshot

PERMANENT = frozenset({ledger_graph.GATED_LABEL, ledger_graph.APPROVAL_LABEL, *label_style.KIND_LABELS})


def field_names(snapshot: Snapshot, mirror: Mirror) -> set[str]:
    """Every field name in use on an item, or mapped to a board field by the mirror (`field:NAME`)."""
    names = {name for item in snapshot.items.values() for name, _values in item.fields}
    mapping = mirror.options.get("project_fields")
    if isinstance(mapping, dict):
        names |= {source[len("field:"):] for source in mapping.values() if isinstance(source, str) and source.startswith("field:")}
    return names


def is_retired_kind(name: str, fields: set[str]) -> bool:
    """Whether the name is of a kind qew retires (before asking whether anything still carries or wants it)."""
    if name in PERMANENT or label_style.is_priority(name) or name in label_style.STATUS_HEX:
        return False
    prefix, colon, _value = name.partition(":")
    return label_style.is_managed(name) or (bool(colon) and prefix in fields)


def retired(ledger: Ledger, mirror: Mirror, forge: Forge) -> list[str]:
    """The retired label definitions on the forge that nothing carries and no item will apply."""
    snapshot = ledger.snapshot()
    overlay = mirror.mode == "overlay"
    wanted = {label for item_id in sync_plan.selected(snapshot, mirror.select)
              for label in sync_plan.labels(snapshot, snapshot.items[item_id], overlay)}
    in_use = forge.labels_in_use()
    fields = field_names(snapshot, mirror)
    return [name for name in forge.list_repo_labels()
            if is_retired_kind(name, fields) and name not in in_use and name not in wanted]


def prune(ledger: Ledger, config: Config, mirrors: list[Mirror], dry_run: bool,
          build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    """Refuses before any call when a chosen target is read-only; then lists (dry run) or deletes per target."""
    slugs = [ledger_config.mirror_slug(m) for m in mirrors]
    read_only = [s for m, s in zip(mirrors, slugs) if ledger_config.is_read_only(config, m)]
    if read_only:
        raise QewError(f"{', '.join(read_only)}: read-only target, labels are never pruned there; name a writable one with --target", 2)
    lines: list[str] = []
    for mirror, slug in zip(mirrors, slugs):
        forge = build(mirror)
        names = retired(ledger, mirror, forge)
        if not names:
            lines.append(f"{slug}: no retired labels")
        for name in names:
            if not dry_run:
                forge.delete_label(name)
            lines.append(f"{slug}: {'would delete' if dry_run else 'deleted'} label {name}")
    return "\n".join(lines)

