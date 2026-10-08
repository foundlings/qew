"""Commands that create, change and read items: init, create, set, show, list, ready, blocked, graph."""

import json
from pathlib import Path
import sys
from typing import Callable, Dict, List, Optional

from qew import graph_rules, ledger_config, ledger_labels
from qew.errors import QewError
from qew.ledger_config import CONFIG, LEDGER_FILE, Config
from qew.ledger_event import Event, State, fold, parse_event
from qew.ledger_ids import valid_id
from qew.ledger_item import DEFAULT_PRIORITY, Item, Kind, from_header
from qew.ledger_store import Ledger, actor, now, open_ledger
from qew.requester import require_quote


def ledger_for(config: Config) -> Ledger:
    return open_ledger(config.ledger_path, config.prefix)


def init(ledger: Optional[str]) -> str:
    root = Path.cwd().resolve()
    config_path = root / CONFIG
    created = []
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f'[ledger]\npath = "{ledger or "../" + root.name + "-ledger"}"\n',
            encoding="utf-8",
        )
        created.append(str(CONFIG))
    config = ledger_config.load(root)
    marker = config.ledger_path / LEDGER_FILE
    if not marker.exists():
        config.ledger_path.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f'name = "{config.ledger_path.name}"\nschema_version = 1\n# id_mode = "time"  # or "letters" / "counter", or a table: [id_mode] epic = "letters"\n',
            encoding="utf-8",
        )
        created.append(str(marker))
    return "\n".join(f"created {path}" for path in created) or "already initialized"


def set_fields(
    config: Config, references: List[str], changes: Dict[str, str], body: Optional[str], allow_closed: bool = False
) -> List[str]:
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    if "labels" in changes:
        ledger_labels.require_valid(changes["labels"].split())
    ids = list(dict.fromkeys(ledger.resolve(reference, list(snapshot.items)) for reference in references))
    if len(ids) > 1 and (body is not None or "title" in changes):
        raise QewError("--title and --body name one item; several items may share parent, needs, labels or priority", 2)
    for item_id in ids:
        state = snapshot.folds[item_id].state
        if state in (State.COMPLETED, State.CLOSED) and not allow_closed:
            raise QewError(f"{item_id} is {state.value}; pass --closed to edit finished work", 2)
    proposed = dict(snapshot.items)
    audit = []  # (id, key, old, new) in write order
    for item_id in ids:
        item = proposed[item_id]
        for key, value in changes.items():
            if key in ("needs", "parent"):
                value = " ".join(ledger.resolve(part, list(snapshot.items)) for part in value.split())
            old = item.header()[key]
            if old == value:
                continue
            try:
                item = item.with_value(key, value)
            except QewError as error:
                raise QewError(str(error).split(": ", 1)[-1], 2) from None
            audit.append((item_id, key, old, value))
        proposed[item_id] = item
    graph_rules.validate_edges(proposed, ids)
    lines = []
    for item_id in ids:
        original = snapshot.items[item_id]
        if proposed[item_id] != original:
            ledger.write_item(proposed[item_id], overwrite=True)
        for _, key, old, new in (entry for entry in audit if entry[0] == item_id):
            ledger.append_event(item_id, "field", key=key, old=old, new=new)
            lines.append(f"{item_id} {key}: {old} -> {new}")
        if body is not None and body != original.body:
            ledger.write_item(Item(**{**proposed[item_id].__dict__, "body": body}), overwrite=True)
            ledger.append_event(item_id, "field", key="body", old="", new="(changed)")
            lines.append(f"{item_id} body changed")
    return lines or [f"{' '.join(ids)} unchanged"]
