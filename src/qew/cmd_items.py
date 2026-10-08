"""Commands that create, change and read items: init, create, set, show, list, ready, blocked, graph."""

import json
from pathlib import Path
import sys
from dataclasses import replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

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


Change = Tuple[str, str, str, str]  # item id, key, old, new


def set_fields(
    config: Config,
    references: List[str],
    changes: Dict[str, str],
    body: Optional[str],
    allow_closed: bool = False,
    add_labels: Sequence[str] = (),
    remove_labels: Sequence[str] = (),
) -> List[Change]:
    """Change header fields and the body of one or more items; every change is audited and read back."""
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    if "labels" in changes and (add_labels or remove_labels):
        raise QewError("--labels replaces the labels; use it alone or use --add-label and --remove-label", 2)
    ledger_labels.require_valid(changes["labels"].split() if "labels" in changes else add_labels)
    ids = list(dict.fromkeys(ledger.resolve(reference, list(snapshot.items)) for reference in references))
    if len(ids) > 1 and (body is not None or "title" in changes):
        raise QewError("--title and --body name one item; several items may share parent, needs, labels or priority", 2)
    for item_id in ids:
        state = snapshot.folds[item_id].state
        if state in (State.COMPLETED, State.CLOSED) and not allow_closed:
            raise QewError(f"{item_id} is {state.value}; pass --closed to edit finished work", 2)
    proposed = dict(snapshot.items)
    audit: List[Change] = []  # in write order
    for item_id in ids:
        item = proposed[item_id]
        wanted = dict(changes)
        if add_labels or remove_labels:
            kept = [label for label in item.labels if label not in remove_labels]
            wanted["labels"] = " ".join(kept + [label for label in dict.fromkeys(add_labels) if label not in kept])
        for key, value in wanted.items():
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
        if body is not None and body.rstrip("\n") != item.body.rstrip("\n"):
            item = replace(item, body=body)
            audit.append((item_id, "body", "", "(changed)"))
        proposed[item_id] = item
    graph_rules.validate_edges(proposed, ids)
    for item_id in ids:
        if any(entry[0] == item_id for entry in audit):
            ledger.write_item(proposed[item_id], overwrite=True)
            for _, key, old, new in (entry for entry in audit if entry[0] == item_id):
                ledger.append_event(item_id, "field", key=key, old=old, new=new)
            stored = ledger.load_item(item_id)
            if stored != proposed[item_id] or stored.body.rstrip("\n") != proposed[item_id].body.rstrip("\n"):
                raise QewError(f"{item_id}: the item did not read back as written", 3)
    return audit
