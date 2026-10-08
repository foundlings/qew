"""Read and write the ledger's files. Every write makes a new file or edits one item file."""

from dataclasses import dataclass, field, replace
from functools import cached_property
from datetime import datetime, timezone
import json
import os
import tomllib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from qew.errors import QewError
from qew.ledger_config import LEDGER_FILE
from qew.ledger_event import Event, Folded, fold, needs_after, parse_event
from qew.ledger_item import Item, parse_item, render_item
from qew.ledger_ids import event_id, next_id, parse_modes, sort_key

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def actor() -> str:
    return os.environ.get("QEW_ACTOR") or os.environ.get("USER") or "unknown"


@dataclass(frozen=True)
class Ledger:
    path: Path
    prefix: str = ""
    id_modes: Dict[str, str] = field(default_factory=lambda: {"epic": "time", "issue": "time", "task": "time"})

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
        """Mirror records: the committed identity merged with this machine's local sync state."""
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
            mirrors.append({**value, **self.read_state(item_id, path.stem), "_target": path.stem})
        return mirrors

    # -- local sync state: what this machine knows about a forge, never committed
    def state_path(self, item_id: str, target: str) -> Path:
        return self.path / ".qew" / "state" / target / f"{item_id}.json"

    def read_state(self, item_id: str, target: str) -> dict:
        path = self.state_path(item_id, target)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}  # a damaged cache only costs a redundant forge update; the ledger is untouched
        return value if isinstance(value, dict) else {}

    def snapshot(self) -> "Snapshot":
        items: Dict[str, Item] = {}
        folds: Dict[str, Folded] = {}
        events: Dict[str, List[Event]] = {}
        for item_id in self.item_ids():
            events[item_id] = self.load_events(item_id)
            raw = self.load_item(item_id)
            items[item_id] = replace(raw, needs=needs_after(raw.needs, events[item_id]))
            folds[item_id] = fold(events[item_id])
        return Snapshot(self, items, folds, events)

    # -- resolving
    def resolve(self, reference: str, ids: Optional[Iterable[str]] = None) -> str:
        """Full ID from an exact ID, an exact ID ignoring case, or a unique prefix."""
        known = list(ids if ids is not None else self.item_ids())
        text = reference.strip()
        if not text:
            raise QewError("an item id is required", 2)
        if text in known:
            return text
        folded = [item for item in known if item.lower() == text.lower()]
        if len(folded) == 1:
            return folded[0]
        matches = [item for item in known if item.lower().startswith(text.lower())]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise QewError(f"no item matches `{reference}`", 2)
        raise QewError(f"`{reference}` is ambiguous: {', '.join(sorted(matches, key=sort_key)[:8])}", 2)

    # -- writing
    def write_item(self, item: Item, overwrite: bool = False) -> None:
        self.items_dir.mkdir(parents=True, exist_ok=True)
        path = self.items_dir / f"{item.id}.md"
        _write(path, render_item(item), exclusive=not overwrite)

    def append_event(self, item_id: str, event_type: str, at: str = "", by: str = "", **data: object) -> Event:
        directory = self.events_dir / item_id
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(path.stem for path in directory.glob("*.json"))
        who = by or actor()
        event = Event(event_id(existing[-1] if existing else ""), event_type, at or now(), who, dict(data))
        _write(directory / f"{event.id}.json", event.to_json(), exclusive=True)
        return event

    def write_mirror(self, item_id: str, target: str, value: dict) -> None:
        """Identity keys go to the committed mirror file (written once); everything else is local state."""
        identity = {k: v for k, v in value.items() if k in MIRROR_IDENTITY}
        volatile = {k: v for k, v in value.items() if k not in MIRROR_IDENTITY and k != "_target"}
        directory = self.mirrors_dir / item_id
        path = directory / f"{target}.json"
        current = {}
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                current = {}
        if {k: current.get(k) for k in identity} != identity or not path.is_file():
            directory.mkdir(parents=True, exist_ok=True)
            merged = {**{k: v for k, v in current.items() if k in MIRROR_IDENTITY}, **identity}
            _write(path, json.dumps(merged, sort_keys=True, indent=2) + "\n", exclusive=False)
        state_path = self.state_path(item_id, target)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _write_local(state_path, json.dumps({**self.read_state(item_id, target), **volatile}, sort_keys=True, indent=2) + "\n")

    def remove_mirror(self, item_id: str, target: str) -> None:
        """Forget one mirror file (journaled like every write, so the session commits the deletion)."""
        path = self.mirrors_dir / item_id / f"{target}.json"
        if JOURNAL is not None:
            JOURNAL.append((path, path.read_text(encoding="utf-8") if path.exists() else None))
        path.unlink(missing_ok=True)
        self.state_path(item_id, target).unlink(missing_ok=True)

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

    @property
    def views_dir(self) -> Path:
        return self.path / "views"

    def write_view(self, name: str, value: dict, overwrite: bool) -> None:
        """One file per named board view; a new name never replaces an existing file."""
        self.views_dir.mkdir(parents=True, exist_ok=True)
        _write(self.views_dir / f"{name}.json", json.dumps(value, sort_keys=True, indent=2) + "\n", exclusive=not overwrite)

    def write_board(self, target: str, value: dict) -> None:
        directory = self.path / "boards"
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / f"{target}.json", json.dumps(value, sort_keys=True, indent=2) + "\n", exclusive=False)

    def new_id(self, kind: str, taken: Optional[Iterable[str]] = None) -> str:
        return next_id(self.id_modes[kind], taken if taken is not None else self.item_ids())


@dataclass(frozen=True)
class Snapshot:
    ledger: Ledger
    items: Dict[str, Item]
    folds: Dict[str, Folded]
    events: Dict[str, List[Event]]

    def short(self, item_id: str) -> str:
        return item_id

    @cached_property
    def derived(self):
        """item id -> (effective state, holders); an epic is in progress while a direct child is claimed."""
        from qew import epic_claims

        return epic_claims.derive(self)


def open_ledger(path: Path, prefix: str) -> Ledger:
    if not (path / LEDGER_FILE).is_file():
        raise QewError(f"{path} is not a ledger ({LEDGER_FILE} is missing); run `qew init`", 2)
    try:
        data = tomllib.loads((path / LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{path / LEDGER_FILE}: {error}", 1) from None
    return Ledger(path, prefix, parse_modes(data.get("id_mode"), ("epic", "issue", "task")))


MIRROR_IDENTITY = ("forge", "url", "kind", "number", "node_id", "issue_url", "head")
JOURNAL: Optional[List[Tuple[Path, Optional[str]]]] = None  # (path, previous text or None) while a session records


def journal_start() -> None:
    global JOURNAL
    JOURNAL = []


def journal_take() -> List[Tuple[Path, Optional[str]]]:
    global JOURNAL
    taken, JOURNAL = JOURNAL or [], None
    return taken


def journal_rollback(entries: List[Tuple[Path, Optional[str]]]) -> None:
    """Undo recorded writes, newest first: restore previous text or delete a new file."""
    for path, previous in reversed(entries):
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")


def _write_local(path: Path, text: str) -> None:
    """Atomic write of a local cache file (not journaled, not committed)."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _write(path: Path, text: str, exclusive: bool) -> None:
    """Write whole or not at all: a temporary file then an atomic link or rename."""
    previous = path.read_text(encoding="utf-8") if JOURNAL is not None and path.exists() else None
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
        if JOURNAL is not None:
            JOURNAL.append((path, previous))
    except OSError as error:
        raise QewError(f"cannot write {path}: {error}", 3) from None
    finally:
        if temporary.exists():
            temporary.unlink()
