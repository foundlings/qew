"""Migrate item ids to time mode: rename the items and carry every reference to the new ids.

Each old id is kept in the item's `aliases:` header, so lookups by it still resolve (see ledger_aliases).
The work is ordered so that the ledger passes `qew check` after every single file operation: items are
renamed first (the alias map then resolves the old references, directories and leftover files), then
references, event files, mirror files and named board views follow. A rerun derives what is left from
the files alone and finishes it; a finished ledger has nothing left to do.
"""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from qew import ledger_store
from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_ids import is_time_id, next_id, sort_key
from qew.ledger_item import Item
from qew.ledger_store import Ledger

LINK_FIELDS = ("parent", "needs", "tracks", "pr", "discovered_from")
VIEW_ID_TERMS = ("epic", "parent")  # board filter terms whose values are item ids


@dataclass(frozen=True)
class Report:
    renames: Tuple[Tuple[str, str], ...]  # (old id, new id) for every item migrated by this run
    rewritten: int  # files moved, rewritten or removed to follow the renames (this run)

    @property
    def nothing(self) -> bool:
        return not self.renames and not self.rewritten


def migrate(ledger: Ledger, actor_name: str, dry_run: bool = False) -> Report:
    """Migrate every item whose id is not a time id; with `dry_run` only count what would change."""
    aliases = ledger.aliases()
    snapshot = ledger.snapshot()
    for item_id, folded in sorted(snapshot.folds.items(), key=lambda pair: sort_key(pair[0])):
        if folded.state == State.CLAIMED and folded.owner != actor_name:
            raise QewError(f"{item_id} is claimed by {folded.owner}; ids are not migrated under another login's claim "
                           f"(ask them to release it, or run as that login)", 3)
    pending = sorted((i for i in ledger.item_ids() if not is_time_id(i)),
                     key=lambda i: (snapshot.items[i].created, sort_key(i)))
    taken = {*ledger.item_ids(), *aliases.by_alias}
    renames: List[Tuple[str, str]] = []
    for old in pending:
        new = next_id("time", taken)
        taken.add(new)
        renames.append((old, new))
    mapping = {**aliases.by_alias, **dict(renames)}
    if not mapping:
        return Report((), 0)
    apply = not dry_run
    rewritten = 0
    for old, new in renames:
        if apply:
            item = ledger.load_item(old)
            ledger.write_item(replace(item, id=new, aliases=(*item.aliases, old)))
            ledger_store.discard(ledger.items_dir / f"{old}.md")
    for stem in sorted(aliases.superseded):
        rewritten += 1
        if apply:
            ledger_store.discard(ledger.items_dir / f"{stem}.md")
    rewritten += _follow_links(ledger, mapping, {old for old, _ in renames}, apply)
    rewritten += _move_event_files(ledger, mapping, apply)
    rewritten += _move_mirror_files(ledger, mapping, apply)
    rewritten += _rewrite_views(ledger, mapping, apply)
    if apply:
        _move_local_state(ledger, mapping)
    return Report(tuple(renames), rewritten)


def _map_tokens(text: str, mapping: Dict[str, str]) -> str:
    return " ".join(mapping.get(token, token) for token in text.split())


def _follow_links(ledger: Ledger, mapping: Dict[str, str], renamed: set, apply: bool) -> int:
    """Item headers: parent, needs, tracks, pr and discovered_from name the current ids."""
    count = 0
    for item_id in ledger.item_ids():
        if item_id in renamed and not apply:
            continue  # not yet renamed on disk; its links are rewritten as part of the rename's follow-up
        raw = ledger.load_item(item_id)
        fixed = _linked(raw, mapping)
        if fixed != raw:
            count += 1
            if apply:
                ledger.write_item(fixed, overwrite=True)
    return count


def _linked(item: Item, mapping: Dict[str, str]) -> Item:
    return replace(
        item,
        parent=mapping.get(item.parent, item.parent),
        needs=tuple(mapping.get(n, n) for n in item.needs),
        tracks=mapping.get(item.tracks, item.tracks),
        pr=mapping.get(item.pr, item.pr),
        discovered_from=mapping.get(item.discovered_from, item.discovered_from),
    )


def _rewrite_event(text: str, mapping: Dict[str, str]) -> str:
    """The event file text naming current ids; the text itself when it cites none (or is not an event)."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if not isinstance(value, dict):
        return text
    changed = False

    def follow(key: str, tokens: bool) -> None:
        nonlocal changed
        current = value.get(key)
        if not isinstance(current, str):
            return
        fixed = _map_tokens(current, mapping) if tokens else mapping.get(current, current)
        if fixed != current:
            value[key] = fixed
            changed = True

    kind = value.get("type")
    if kind == "edge":
        follow("need", False)
        follow("old", True)
        follow("new", True)
    elif kind == "closed":
        follow("duplicate_of", False)
        follow("superseded_by", False)
    elif kind == "field" and value.get("key") in LINK_FIELDS:
        follow("old", True)
        follow("new", True)
    return json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n" if changed else text


def _directories(root: Path) -> List[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []


def _move_event_files(ledger: Ledger, mapping: Dict[str, str], apply: bool) -> int:
    count = 0
    for directory in _directories(ledger.events_dir):
        home = ledger.events_dir / mapping.get(directory.name, directory.name)
        for path in sorted(directory.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            fixed = _rewrite_event(text, mapping)
            if home != directory:
                count += 1
                if apply:
                    home.mkdir(parents=True, exist_ok=True)
                    if not (home / path.name).exists():
                        ledger_store._write(home / path.name, fixed, exclusive=True)
                    ledger_store.discard(path)
            elif fixed != text:
                count += 1
                if apply:
                    ledger_store._write(path, fixed, exclusive=False)
        if apply and home != directory:
            _remove_empty(directory)
    return count


def _move_mirror_files(ledger: Ledger, mapping: Dict[str, str], apply: bool) -> int:
    """Mirror files follow their item; a pull request mirror's `items` list names current ids. Forge numbers never change."""
    count = 0
    for directory in _directories(ledger.mirrors_dir):
        home = ledger.mirrors_dir / mapping.get(directory.name, directory.name)
        for path in sorted(directory.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            fixed = _rewrite_mirror(text, mapping)
            if home != directory:
                count += 1
                if apply:
                    home.mkdir(parents=True, exist_ok=True)
                    if not (home / path.name).exists():
                        ledger_store._write(home / path.name, fixed, exclusive=True)
                    ledger_store.discard(path)
            elif fixed != text:
                count += 1
                if apply:
                    ledger_store._write(path, fixed, exclusive=False)
        if apply and home != directory:
            _remove_empty(directory)
    return count


def _rewrite_mirror(text: str, mapping: Dict[str, str]) -> str:
    try:
        value = json.loads(text)
    except ValueError:
        return text
    items = value.get("items") if isinstance(value, dict) else None
    if not isinstance(items, list):
        return text
    fixed = [mapping.get(i, i) if isinstance(i, str) else i for i in items]
    if fixed == items:
        return text
    value["items"] = fixed
    return json.dumps(value, sort_keys=True, indent=2) + "\n"


def _rewrite_views(ledger: Ledger, mapping: Dict[str, str], apply: bool) -> int:
    count = 0
    for path in sorted(ledger.views_dir.glob("*.json")) if ledger.views_dir.is_dir() else []:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        expression = value.get("filter") if isinstance(value, dict) else None
        if not isinstance(expression, str):
            continue
        fixed = " ".join(_filter_term(term, mapping) for term in expression.split())
        if fixed != expression:
            count += 1
            if apply:
                ledger_store._write(path, json.dumps({**value, "filter": fixed}, sort_keys=True, indent=2) + "\n", exclusive=False)
    return count


def _filter_term(term: str, mapping: Dict[str, str]) -> str:
    key, colon, values = term.partition(":")
    if not colon or key not in VIEW_ID_TERMS:
        return term
    return key + ":" + ",".join(mapping.get(v, v) for v in values.split(","))


def _move_local_state(ledger: Ledger, mapping: Dict[str, str]) -> None:
    """This machine's sync baselines are cached under the item id; move them with it (never committed)."""
    root = ledger.path / ".qew" / "state"
    for old, new in mapping.items():
        for path in sorted(root.glob(f"*/{old}.json")) if root.is_dir() else []:
            target = path.with_name(f"{new}.json")
            if target.exists():
                path.unlink()
            else:
                os.replace(path, target)


def _remove_empty(directory: Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass  # something else still lives there; the next run moves it
