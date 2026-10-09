"""`qew project status`: compare the board with the ledger and report drift, never writing.

The report is advisory: forge state is never read back as truth for the ledger. It only says the forge disagrees.
"""

import json
from collections.abc import Callable

from qew import board_cards, forge_registry, ledger_config, sync_plan
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards
from qew.forge import COLUMNS, Board, Card, Forge, ForgeError, Unsupported
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger, Snapshot

SCHEMA_VERSION = 1


def repository_of(mirror: Mirror) -> str:
    """OWNER/REPO of the mirror's URL, the key a card's repository is compared with."""
    return "/".join(mirror.repo.split("://", 1)[-1].rstrip("/").split("/")[1:])


def status(config: Config, wanted: str | None, as_json: bool,
           build: Callable[[Mirror], Forge] = forge_registry.build) -> tuple[str, int]:
    """(output, exit code): 0 in sync, 1 drifted or a forge failure, 2 no board to compare with."""
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    lines: list[str] = []
    code = 0
    for mirror in choose(mirrors_with_boards(ledger, config), wanted):
        slug = ledger_config.mirror_slug(mirror)
        if mirror.project is None:
            lines.append(json.dumps({"schema_version": SCHEMA_VERSION, "target": slug, "error": "no board"})
                         if as_json else f"{slug}: no board recorded or configured; run `qew project ensure`")
            code = max(code, 2)
            continue
        try:
            cards = board_cards.local_cards(
                build(mirror).read_board_cards(Board(mirror.project, "", COLUMNS)), repository_of(mirror))
        except Unsupported:
            lines.append(json.dumps({"schema_version": SCHEMA_VERSION, "target": slug, "in_sync": True, "unsupported": True})
                         if as_json else f"{slug}: boards unsupported")
            continue
        except ForgeError as error:
            lines.append(json.dumps({"schema_version": SCHEMA_VERSION, "target": slug, "error": error.detail})
                         if as_json else f"{slug}: failed {error.detail}")
            code = max(code, 1)
            continue
        drift = _drift(ledger, snapshot, mirror, slug, cards)
        if any(drift.values()):
            code = max(code, 1)
        lines.append(_render(slug, drift, as_json))
    return "\n".join(lines) or "no mirror targets configured", code


def _drift(ledger: Ledger, snapshot: Snapshot, mirror: Mirror, slug: str, cards: dict[int, Card]) -> dict[str, list[str]]:
    drift: dict[str, list[str]] = {"missing": [], "stale_column": [], "stale_priority": []}
    for item_id in sync_plan.topological(snapshot, sync_plan.selected(snapshot, mirror.select)):
        record = next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug and m.get("kind") == "issue"), None)
        card = cards.get(record["number"]) if record else None
        if card is None:
            drift["missing"].append(item_id)
            continue
        if card.column != sync_plan.column(snapshot, item_id):
            drift["stale_column"].append(item_id)
        if card.priority != sync_plan.priority_option(snapshot.items[item_id].priority)[0]:
            drift["stale_priority"].append(item_id)
    return drift


def _render(slug: str, drift: dict[str, list[str]], as_json: bool) -> str:
    in_sync = not any(drift.values())
    if as_json:
        return json.dumps({"schema_version": SCHEMA_VERSION, "target": slug, "in_sync": in_sync, **drift,
                           "fix": "" if in_sync else "qew sync"})
    if in_sync:
        return f"{slug}: in sync"
    parts = [f"{name} {' '.join(ids)}" for name, ids in drift.items() if ids]
    return f"{slug}: drifted: " + "; ".join(parts) + "; run `qew sync`"
