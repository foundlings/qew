"""Read and write the ledger's files. Every write makes a new file or edits one item file."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from qew.errors import QewError
from qew.ledger_config import LEDGER_FILE
from qew.ledger_event import Event, Folded, fold, parse_event
from qew.ledger_item import Item, parse_item, render_item
from qew.ulid import new_ulid

MIN_ALIAS = 8


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def actor() -> str:
    return os.environ.get("QEW_ACTOR") or os.environ.get("USER") or "unknown"


@dataclass(frozen=True)
class Ledger:
    path: Path
    prefix: str

    @property
    def items_dir(self) -> Path:
        return self.path / "items"

    @property
    def events_dir(self) -> Path:
        return self.path / "events"

    @property
    def mirrors_dir(self) -> Path:
        return self.path / "mirrors"

    # -- reading
    def item_ids(self) -> List[str]:
        if not self.items_dir.is_dir():
            return []
        return sorted(path.stem for path in self.items_dir.glob("*.md"))

    def load_item(self, item_id: str) -> Item:
        path = self.items_dir / f"{item_id}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"{path}: {error}", 1) from None
        item = parse_item(text, str(path))
        if item.id != item_id:
            raise QewError(f"{path}: header id {item.id} does not match the file name", 1)
        return item

    def load_events(self, item_id: str) -> List[Event]:
        directory = self.events_dir / item_id
        if not directory.is_dir():
            return []
        events = []
        for path in sorted(directory.glob("*.json")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise QewError(f"{path}: {error}", 1) from None
            events.append(parse_event(path.stem, text, str(path)))
        return events

    def load_mirrors(self, item_id: str) -> List[dict]:
        directory = self.mirrors_dir / item_id
        if not directory.is_dir():
            return []
        mirrors = []
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as error:
                raise QewError(f"{path}: {error}", 1) from None
            if not isinstance(value, dict):
                raise QewError(f"{path}: a mirror is one JSON object", 1)
            mirrors.append({**value, "_target": path.stem})
        return mirrors

    def snapshot(self) -> "Snapshot":
        items: Dict[str, Item] = {}
        folds: Dict[str, Folded] = {}
        events: Dict[str, List[Event]] = {}
        for item_id in self.item_ids():
            items[item_id] = self.load_item(item_id)
            events[item_id] = self.load_events(item_id)
            folds[item_id] = fold(events[item_id])
        return Snapshot(self, items, folds, events)

    # -- resolving
    def resolve(self, reference: str, ids: Optional[Iterable[str]] = None) -> str:
        """Full ID from a full ID, an unprefixed ULID prefix, or a prefixed alias."""
        known = list(ids if ids is not None else self.item_ids())
        text = reference.strip().upper()
        if not text:
            raise QewError("an item id is required", 2)
        if text in known:
            return text
        body = text if "-" in text else f"{self.prefix}-{text}"
        matches = [item for item in known if item.startswith(body)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise QewError(f"no item matches `{reference}`", 2)
        raise QewError(f"`{reference}` is ambiguous: {', '.join(short_ids(matches, known))}", 2)

    # -- writing
    def write_item(self, item: Item, overwrite: bool = False) -> None:
        self.items_dir.mkdir(parents=True, exist_ok=True)
        path = self.items_dir / f"{item.id}.md"
        _write(path, render_item(item), exclusive=not overwrite)

    def append_event(self, item_id: str, event_type: str, **data: object) -> Event:
        directory = self.events_dir / item_id
        directory.mkdir(parents=True, exist_ok=True)
        event = Event(new_ulid(), event_type, now(), actor(), dict(data))
        _write(directory / f"{event.id}.json", event.to_json(), exclusive=True)
        return event

    def write_mirror(self, item_id: str, target: str, value: dict) -> None:
        directory = self.mirrors_dir / item_id
        directory.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, sort_keys=True, indent=2) + "\n"
        _write(directory / f"{target}.json", text, exclusive=False)

    def read_board(self, target: str) -> Optional[dict]:
        path = self.path / "boards" / f"{target}.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise QewError(f"{path}: {error}", 1) from None
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise QewError(f"{path}: a board record needs a string `id`", 1)
        return value

    def write_board(self, target: str, value: dict) -> None:
        directory = self.path / "boards"
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / f"{target}.json", json.dumps(value, sort_keys=True, indent=2) + "\n", exclusive=False)

    def new_id(self) -> str:
        return f"{self.prefix}-{new_ulid()}"


@dataclass(frozen=True)
class Snapshot:
    ledger: Ledger
    items: Dict[str, Item]
    folds: Dict[str, Folded]
    events: Dict[str, List[Event]]

    def short(self, item_id: str) -> str:
        return short_ids([item_id], list(self.items))[0]


def short_ids(wanted: List[str], universe: List[str]) -> List[str]:
    """The shortest unique prefix of at least MIN_ALIAS ULID characters, kept with the prefix."""
    result = []
    for item_id in wanted:
        prefix, _, ulid = item_id.partition("-")
        length = MIN_ALIAS
        while length < len(ulid) and sum(1 for other in universe if other.startswith(f"{prefix}-{ulid[:length]}")) > 1:
            length += 1
        result.append(f"{prefix}-{ulid[:length]}")
    return result


def open_ledger(path: Path, prefix: str) -> Ledger:
    if not (path / LEDGER_FILE).is_file():
        raise QewError(f"{path} is not a ledger ({LEDGER_FILE} is missing); run `qew init`", 2)
    return Ledger(path, prefix)


def _write(path: Path, text: str, exclusive: bool) -> None:
    """Write whole or not at all: a temporary file then an atomic link or rename."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        if exclusive:
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise QewError(f"{path} already exists; ledger files are never overwritten here", 1) from None
        else:
            os.replace(temporary, path)
    except OSError as error:
        raise QewError(f"cannot write {path}: {error}", 3) from None
    finally:
        if temporary.exists():
            temporary.unlink()
