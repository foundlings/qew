"""A pull request's link declaration: which ledger items it closes, leaves open, or only relates to (schema 9).

The declaration is a set of event files under `prs/_xx/<target>-pr-<N>/`, one file per change with a unique time-ordered id, never
rewritten: two clones declaring links for one pull request add different files and merge without a conflict. The declaration
is the fold of the files in id order, the later event of an item winning. Forge text is never an input.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from qew import ledger_layout, ledger_store
from qew.errors import QewError
from qew.ledger_ids import event_id
from qew.ledger_store import Ledger, actor, now

Role = Literal["closes", "remains", "related"]
ROLES: tuple[Role, ...] = ("closes", "remains", "related")
LINKS_SCHEMA = 9


@dataclass(frozen=True)
class Declaration:
    """What the ledger says a pull request does to items: each item's role, or why it relates to none."""

    roles: dict[str, Role] = field(default_factory=dict)
    no_item: str | None = None

    def items(self, role: Role) -> list[str]:
        return sorted(item_id for item_id, held in self.roles.items() if held == role)

    @property
    def declared(self) -> bool:
        return bool(self.roles) or self.no_item is not None


def require_schema(ledger: Ledger) -> None:
    """Link declarations need schema 9; a compat (schema 8) ledger refuses, politely, before anything is written."""
    if ledger.schema < LINKS_SCHEMA:
        raise QewError(f"this ledger is schema {ledger.schema}: pull request links start once the ledger is on schema {LINKS_SCHEMA}, "
                       "which the owner moves to in one coordinated step (`qew migrate-layout`, after every agent runs qew 7.0.0 or newer)", 2)


def directory(ledger: Ledger, key: str) -> Path:
    return ledger.path / "prs" / ledger_layout.shard(key) / key


def _events(ledger: Ledger, key: str) -> list[tuple[str, dict[str, object]]]:
    found: list[tuple[str, dict[str, object]]] = []
    where = directory(ledger, key)
    for path in sorted(where.glob("*.json")) if where.is_dir() else []:
        try:
            value: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise QewError(f"{path}: {error}", 1) from None
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise QewError(f"{path}: a pull request event is one JSON object with a `type`", 1)
        found.append((path.stem, cast(dict[str, object], value)))
    return found


def load(ledger: Ledger, key: str) -> Declaration:
    """The declaration of the pull request recorded as `key` (an empty one when nothing was declared)."""
    roles: dict[str, Role] = {}
    no_item: str | None = None
    for name, event in _events(ledger, key):
        kind, item_id, role, reason = event["type"], event.get("item"), event.get("role"), event.get("reason")
        if kind == "link" and isinstance(item_id, str) and role in ROLES:
            roles[item_id] = role
            no_item = None
        elif kind == "unlink" and isinstance(item_id, str):
            roles.pop(item_id, None)
        elif kind == "no_item" and isinstance(reason, str):
            no_item = reason
            roles.clear()
        else:
            raise QewError(f"{directory(ledger, key) / (name + '.json')}: unknown pull request event `{kind}` (written by a newer qew? upgrade it)", 1)
    return Declaration(roles, no_item)


def _append(ledger: Ledger, key: str, kind: str, **data: str) -> None:
    where = directory(ledger, key)
    existing = sorted(path.stem for path in where.glob("*.json")) if where.is_dir() else []
    identifier = event_id(existing[-1] if existing else "")
    body = {"type": kind, "at": now(), "by": actor(), **data}
    ledger_store.write_new(where / f"{identifier}.json", json.dumps(body, sort_keys=True, indent=2) + "\n")


def link(ledger: Ledger, key: str, wanted: dict[str, Role]) -> list[str]:
    """Record each wanted (item, role) that differs from the declaration; returns the items changed (none: nothing written)."""
    require_schema(ledger)
    current = load(ledger, key)
    changed = [item_id for item_id, role in wanted.items() if current.roles.get(item_id) != role]
    for item_id in changed:
        _append(ledger, key, "link", item=item_id, role=wanted[item_id])
    return changed


def unlink(ledger: Ledger, key: str, items: list[str]) -> list[str]:
    """Remove each linked item; one that is not linked is left alone. Returns the items removed."""
    require_schema(ledger)
    current = load(ledger, key)
    removed = [item_id for item_id in items if item_id in current.roles]
    for item_id in removed:
        _append(ledger, key, "unlink", item=item_id)
    return removed


def declare_no_item(ledger: Ledger, key: str, reason: str) -> bool:
    """Declare that the pull request relates to no item; False when it already says exactly that."""
    require_schema(ledger)
    text = reason.strip()
    if not text:
        raise QewError("--no-item needs a reason: say why this pull request relates to no ledger item", 2)
    current = load(ledger, key)
    if current.no_item == text and not current.roles:
        return False
    _append(ledger, key, "no_item", reason=text)
    return True
