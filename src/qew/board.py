"""The offline board: cards derived from a ledger snapshot, laid out in columns. No forge, no network."""

from dataclasses import dataclass
import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence

from qew import sync_plan, untrusted_text
from qew.errors import QewError
from qew.forge import COLUMNS
from qew.jsontypes import JsonObject
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
    labels: tuple[str, ...]
    epic: str
    parent: str
    created: str
    updated: str

    def as_json(self) -> JsonObject:
        return {
            "id": self.id, "short": self.short, "title": self.title, "status": self.status, "state": self.state,
            "kind": self.kind, "priority": self.priority, "owner": self.owner, "labels": list(self.labels),
            "epic": self.epic, "parent": self.parent, "created": self.created, "updated": self.updated,
        }


@dataclass(frozen=True)
class Group:
    name: str
    cards: tuple[Card, ...]
    title: str = ""  # a readable name for groups keyed by an item id
    by_column: bool = True

    @property
    def heading(self) -> str:
        return f"{self.name} {self.title}".strip() if self.title else self.name


def cards(snapshot: Snapshot) -> list[Card]:
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


def by_column(all_cards: Sequence[Card], columns: Sequence[str] = COLUMNS) -> list[Group]:
    """Every wanted column, in order, even when empty; cards keep their given order."""
    return [Group(name, tuple(c for c in all_cards if c.status == name)) for name in columns]


SORT_KEYS = ("priority", "updated", "created", "title")
GROUP_KEYS = ("column", "owner", "label", "epic", "parent")
NONE_GROUP = "(none)"


def sort_cards(all_cards: Sequence[Card], spec: str = "priority") -> list[Card]:
    """`KEY` or `KEY:desc` with KEY one of SORT_KEYS; ties fall back to the item id."""
    key, _, direction = spec.partition(":")
    if key not in SORT_KEYS or direction not in ("", "asc", "desc"):
        raise QewError(f"unknown sort `{spec}`; use one of {', '.join(SORT_KEYS)} optionally followed by :desc", 2)
    ranked = sorted(all_cards, key=lambda c: c.id)
    return sorted(ranked, key=lambda c: getattr(c, key), reverse=direction == "desc")


def group_cards(snapshot: Snapshot, all_cards: Sequence[Card], key: str, columns: Sequence[str] = COLUMNS) -> list[Group]:
    """Sections for `key`. Every card lands in at least one section; a card with several labels lands in each."""
    if key == "column":
        return by_column(all_cards, columns)
    if key not in GROUP_KEYS:
        raise QewError(f"unknown group `{key}`; use one of {', '.join(GROUP_KEYS)}", 2)
    sections: dict[str, list[Card]] = {}
    for card in all_cards:
        names = list(card.labels) if key == "label" else [getattr(card, key)]
        for name in names or [""]:
            sections.setdefault(name or NONE_GROUP, []).append(card)
    named = sorted(n for n in sections if n != NONE_GROUP)
    result = []
    for name in [*named, *([NONE_GROUP] if NONE_GROUP in sections else [])]:
        title = ""
        if key in ("epic", "parent") and name in snapshot.items:
            title = f"{snapshot.short(name)} {snapshot.items[name].title}"
        result.append(Group(name, tuple(sections[name]), title, by_column=False))
    return result


_FILTER_KEYS = ("label", "kind", "state", "priority", "owner", "epic", "parent", "status")


def filter_terms(expression: str) -> list[tuple[str, list[str]]]:
    """The syntax of a filter: (key, alternatives) per space-separated `key:value` term."""
    terms = []
    for term in expression.split():
        key, colon, value = term.partition(":")
        if not colon or key not in _FILTER_KEYS or not value:
            raise QewError(f"filter term `{term}` must be one of {', '.join(k + ':X' for k in _FILTER_KEYS)}", 2)
        terms.append((key, value.split(",")))
    return terms


def parse_filter(snapshot: Snapshot, expression: str) -> Callable[[Card], bool]:
    """A predicate for the terms (all must match; commas in a value are alternatives)."""
    terms = [
        (key, [snapshot.ledger.resolve(o) for o in options] if key in ("epic", "parent") else options)
        for key, options in filter_terms(expression)
    ]

    def matches(card: Card) -> bool:
        values = {
            "label": card.labels, "kind": (card.kind,), "state": (card.state,), "priority": (str(card.priority),),
            "owner": (card.owner,), "epic": (card.epic,), "parent": (card.parent,), "status": (card.status,),
        }
        return all(any(option in values[key] for option in options) for key, options in terms)

    return matches


def render_json(name: str, groups: Sequence[Group]) -> str:
    return json.dumps({
        "version": JSON_VERSION, "board": name,
        "groups": [
            {"name": g.name, "title": g.title, "count": len(g.cards), "cards": [c.as_json() for c in g.cards]}
            for g in groups
        ],
    })


def render_columns(groups: Sequence[Group]) -> str:
    return "\n".join(f"{g.name} {len(g.cards)}" for g in groups)


def render_compact(groups: Sequence[Group]) -> str:
    lines: list[str] = []
    for group in groups:
        lines.append(f"# {_one_line(group.name, COMPACT_TITLE)} {len(group.cards)}")
        lines.extend(f"{c.short} p{c.priority} {c.owner or '-'} {_one_line(c.title, COMPACT_TITLE)}" for c in group.cards)
    return "\n".join(lines)


def render_text(groups: Sequence[Group], width: int = 0, color: bool = False) -> str:
    width = width or terminal_width()
    lines: list[str] = []
    for group in groups:
        head = f"{_one_line(group.heading, width)} ({len(group.cards)})"
        lines.append(f"\x1b[1m{head}\x1b[0m" if color else head)
        for card in group.cards:
            tail = "".join([f" @{card.owner}" if card.owner else "", *[f" #{label}" for label in card.labels]])
            prefix = f"  {card.short} [p{card.priority}] " + ("" if group.by_column else f"{card.status}: ")
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
