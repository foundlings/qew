"""The offline board: cards derived from a ledger snapshot, laid out in columns. No forge, no network."""

from dataclasses import dataclass
import json
import os
import shutil
import sys
from typing import Dict, List, Sequence, Tuple

from qew import sync_plan, untrusted_text
from qew.forge import COLUMNS
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot

JSON_VERSION = 1
COMPACT_TITLE = 40


@dataclass(frozen=True)
class Card:
    id: str
    short: str
    title: str
    status: str
    state: str
    kind: str
    priority: int
    owner: str
    labels: Tuple[str, ...]
    epic: str
    parent: str
    created: str
    updated: str

    def as_json(self) -> dict:
        return {
            "id": self.id, "short": self.short, "title": self.title, "status": self.status, "state": self.state,
            "kind": self.kind, "priority": self.priority, "owner": self.owner, "labels": list(self.labels),
            "epic": self.epic, "parent": self.parent, "created": self.created, "updated": self.updated,
        }


@dataclass(frozen=True)
class Group:
    name: str
    cards: Tuple[Card, ...]


def cards(snapshot: Snapshot) -> List[Card]:
    """One card per item; the status comes from `sync_plan.column`, the same derivation sync uses."""
    return [_card(snapshot, item_id) for item_id in snapshot.items]


def _card(snapshot: Snapshot, item_id: str) -> Card:
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    events = snapshot.events[item_id]
    return Card(
        id=item_id, short=snapshot.short(item_id), title=item.title, status=sync_plan.column(snapshot, item_id),
        state=folded.state.value if folded.state else "", kind=item.kind.value, priority=item.priority,
        owner=folded.owner or "", labels=tuple(item.labels), epic=_epic_of(snapshot, item_id), parent=item.parent,
        created=item.created, updated=max((e.at for e in events), default=item.created),
    )


def _epic_of(snapshot: Snapshot, item_id: str) -> str:
    """The nearest ancestor epic, or "" (an epic's own epic is its parent epic, if any)."""
    seen = {item_id}
    current = snapshot.items[item_id].parent
    while current and current in snapshot.items and current not in seen:
        if snapshot.items[current].kind == Kind.EPIC:
            return current
        seen.add(current)
        current = snapshot.items[current].parent
    return ""


def by_column(all_cards: Sequence[Card], columns: Sequence[str] = COLUMNS) -> List[Group]:
    """Every wanted column, in order, even when empty; cards keep their given order."""
    return [Group(name, tuple(c for c in all_cards if c.status == name)) for name in columns]


def ordered(all_cards: Sequence[Card]) -> List[Card]:
    return sorted(all_cards, key=lambda c: (c.priority, c.id))


def render_json(name: str, groups: Sequence[Group]) -> str:
    return json.dumps({
        "version": JSON_VERSION, "board": name,
        "groups": [{"name": g.name, "count": len(g.cards), "cards": [c.as_json() for c in g.cards]} for g in groups],
    })


def render_columns(groups: Sequence[Group]) -> str:
    return "\n".join(f"{g.name} {len(g.cards)}" for g in groups)


def render_compact(groups: Sequence[Group]) -> str:
    lines: List[str] = []
    for group in groups:
        lines.append(f"# {group.name} {len(group.cards)}")
        lines.extend(f"{c.short} p{c.priority} {c.owner or '-'} {_one_line(c.title, COMPACT_TITLE)}" for c in group.cards)
    return "\n".join(lines)


def render_text(groups: Sequence[Group], width: int = 0, color: bool = False) -> str:
    width = width or terminal_width()
    lines: List[str] = []
    for group in groups:
        head = f"{group.name} ({len(group.cards)})"
        lines.append(f"\x1b[1m{head}\x1b[0m" if color else head)
        for card in group.cards:
            tail = "".join([f" @{card.owner}" if card.owner else "", *[f" #{label}" for label in card.labels]])
            prefix = f"  {card.short} [p{card.priority}] "
            room = max(10, width - len(prefix) - len(tail))
            lines.append(prefix + _one_line(card.title, room) + tail)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def _one_line(title: str, room: int) -> str:
    """The title made safe to print: hidden characters withheld, one line, cut to `room` characters."""
    text = untrusted_text.show(title).text
    return text if len(text) <= room else text[: max(1, room - 1)] + "…"


def terminal_width() -> int:
    try:
        return int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        return shutil.get_terminal_size((80, 24)).columns


def use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ
