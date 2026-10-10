"""Read and write the ledger's files. Every write makes a new file or edits one item file."""

from dataclasses import dataclass, field, replace
from functools import cached_property
from datetime import datetime, UTC
import json
import os
import shutil
import subprocess
import threading
import tomllib
from pathlib import Path
from typing import Any, cast
from collections.abc import Iterable

from qew import attachments, evidence_header, ledger_aliases
from qew.errors import QewError
from qew.mirror_types import MirrorRecord
from qew.ledger_aliases import ITEM_FILE
from qew.ledger_config import LEDGER_FILE
from qew.ledger_event import Event, Folded, State, fold, needs_after, parse_event, valid_actor
from qew.ledger_item import Item, parse_item, read_item_text, render_item
from qew.jsontypes import JsonObject
from qew.ledger_ids import DEFAULT_MODES, event_id, next_id, parse_modes, sort_key

_INTENTS = threading.Lock()  # sync workers add and clear creating intents in one shared file

def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_configured: str | None = None


def _safe(name: str, source: str) -> str:
    """The actor is untrusted text (it may become a forge label): one printable token, else refuse."""
    if not valid_actor(name):
        raise QewError(f"{source} must be one word without spaces, commas or control characters", 3)
    return name


def actor() -> str:
    """Who is acting: QEW_ACTOR, else the configured VCS user name. Never the operating-system login."""
    global _configured
    named = os.environ.get("QEW_ACTOR")
    if named:
        return _safe(named, "QEW_ACTOR")
    if _configured is None:
        for argv in (["git", "config", "user.name"], ["jj", "config", "get", "user.name"]):
            try:
                result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0 and result.stdout.strip():
                _configured = result.stdout.strip()
                break
    if not _configured:
        raise QewError("no identity: run `git config user.name NAME` (or set QEW_ACTOR); qew never uses the OS login", 2)
    # A configured display name is routinely "First Last": flatten whitespace and commas, refuse control characters.
    return _safe("-".join(_configured.replace(",", " ").split()), "git/jj user.name")


@dataclass(frozen=True)
class Ledger:
    path: Path
    prefix: str = ""
    id_modes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODES))
    schema: int = 5  # the declared schema_version; `fields` need 5 (see ledger_config.WORKABLE_SCHEMA)
    evidence_strict: bool = True  # qew-ledger.toml `evidence = "lenient"` opts out of the proof-or-typed-absence rule

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
    def item_ids(self) -> list[str]:
        if not self.items_dir.is_dir():
            return []
        stale = self.aliases().superseded
        return sorted(path.name for path in self.items_dir.iterdir() if (path / ITEM_FILE).is_file() and path.name not in stale)

    def item_path(self, item_id: str) -> Path:
        """The item file: `items/<ID>/item.md`; the id is only in the directory name."""
        return self.items_dir / item_id / ITEM_FILE

    def attachments_dir(self, item_id: str) -> Path:
        return self.items_dir / item_id / attachments.DIRECTORY

    def find_blob(self, item_id: str, blob: str) -> Path | None:
        """The stored file of an attachment, under the item's id or any id it had before a migration."""
        for name in (item_id, *self.aliases().of(item_id)):
            found = self.attachments_dir(name) / blob
            if found.is_file():
                return found
        return None

    def store_blob(self, item_id: str, source: Path, blob: str) -> bool:
        """Copy `source` to the item's immutable blob file: False when those bytes are already stored there.

        Journaled like every write, so a failed command removes what it created."""
        target = self.attachments_dir(item_id) / blob
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        except OSError as error:
            raise QewError(f"cannot store {source} as {target}: {error}", 3) from None
        finally:
            temporary.unlink(missing_ok=True)
        if JOURNAL is not None:
            JOURNAL.append((target, None))
        return True

    def aliases(self) -> ledger_aliases.Aliases:
        return ledger_aliases.scan(self.items_dir)

    def events_homes(self, item_id: str) -> list[Path]:
        return self._homes(self.events_dir, item_id)

    def _homes(self, root: Path, item_id: str) -> list[Path]:
        """The directories that may hold this item's files: under its id, and under any id it had before a migration."""
        return [root / name for name in (item_id, *self.aliases().of(item_id))]

    def load_item(self, item_id: str) -> Item:
        path = self.item_path(item_id)
        try:
            text = read_item_text(path, str(path))
        except (OSError, UnicodeError) as error:
            raise QewError(f"{path}: {error}", 1) from None
        item = parse_item(text, str(path))
        if item.id != item_id:
            raise QewError(f"{path}: header id {item.id} does not match the directory name", 1)
        return item

    def load_events(self, item_id: str) -> list[Event]:
        events = []
        seen = set()
        for directory in self._homes(self.events_dir, item_id):
            for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
                if path.stem in seen:
                    continue  # a migration copied it under the new id before removing the old file
                seen.add(path.stem)
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    raise QewError(f"{path}: {error}", 1) from None
                events.append(parse_event(path.stem, text, str(path)))
        return sorted(events, key=lambda event: event.id)

    def load_mirrors(self, item_id: str) -> list[MirrorRecord]:
        """Mirror records: the committed identity merged with this machine's local sync state."""
        mirrors = []
        seen = set()
        for directory in self._homes(self.mirrors_dir, item_id):
            for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
                if path.stem in seen:
                    continue  # a migration copied it under the new id already
                seen.add(path.stem)
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as error:
                    raise QewError(f"{path}: {error}", 1) from None
                if not isinstance(value, dict):
                    raise QewError(f"{path}: a mirror is one JSON object", 1)
                mirrors.append(cast(MirrorRecord, {**value, **self.read_state(item_id, path.stem), "_target": path.stem}))
        return mirrors

    # -- local sync state: what this machine knows about a forge, never committed
    def state_path(self, item_id: str, target: str) -> Path:
        return self.path / ".qew" / "state" / target / f"{item_id}.json"

    def read_state(self, item_id: str, target: str) -> JsonObject:
        path = next((p for p in (self.state_path(name, target) for name in (item_id, *self.aliases().of(item_id))) if p.is_file()), None)
        if path is None:
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}  # a damaged cache only costs a redundant forge update; the ledger is untouched
        return value if isinstance(value, dict) else {}

    def snapshot(self) -> Snapshot:
        items: dict[str, Item] = {}
        folds: dict[str, Folded] = {}
        events: dict[str, list[Event]] = {}
        aliases = self.aliases()
        for item_id in self.item_ids():
            events[item_id] = self.load_events(item_id)
            raw = self.load_item(item_id)
            items[item_id] = ledger_aliases.canonical_item(replace(raw, needs=needs_after(raw.needs, events[item_id])), aliases)
            folds[item_id] = fold(events[item_id])
        return Snapshot(self, items, folds, events)

    # -- resolving
    def resolve(self, reference: str, ids: Iterable[str] | None = None) -> str:
        """Full ID from an exact ID, an exact ID ignoring case, or a unique prefix."""
        known = list(ids if ids is not None else self.item_ids())
        text = reference.strip()
        if not text:
            raise QewError("an item id is required", 2)
        if text in known:
            return text
        renamed = self.aliases().by_alias.get(text)
        if renamed in known:
            return renamed
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
        if item.fields and self.schema < 5:
            raise QewError(f"{item.id}: fields need schema_version 5; run `qew migrate-layout` first (an older qew cannot read the header key)", 2)
        path = self.item_path(item.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, render_item(item), exclusive=not overwrite)

    def append_event(self, item_id: str, event_type: str, at: str = "", by: str = "", **data: Any) -> Event:
        directory = self.events_dir / item_id
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(path.stem for path in directory.glob("*.json"))
        event = Event(event_id(existing[-1] if existing else ""), event_type, at or now(), by or actor(), dict(data))
        _write(directory / f"{event.id}.json", event.to_json(), exclusive=True)
        if event_type == "completed" and self.item_path(item_id).is_file():
            shown = evidence_header.header_values(event.data)  # the item shows its own proof, or why there is none
            if shown is not None:
                item = self.load_item(item_id)
                self.write_item(replace(item, evidence=shown[0], evidence_absent=shown[1]), overwrite=True)
        return event

    def write_mirror(self, item_id: str, target: str, value: MirrorRecord) -> None:
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

    def cursor_path(self, target: str) -> Path:
        return self.path / ".qew" / "state" / target / "cursor.json"

    def read_cursor(self, target: str) -> JsonObject:
        """The pull cursor of one target (see pull_cursor); {} when absent or damaged."""
        path = self.cursor_path(target)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}  # a damaged cursor only costs one extra full listing
        return value if isinstance(value, dict) else {}

    def write_cursor(self, target: str, value: JsonObject) -> None:
        path = self.cursor_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_local(path, json.dumps(value, sort_keys=True, indent=2) + "\n")

    def pace_dir(self, target: str) -> Path:
        return self.path / ".qew" / "state" / target

    def intent_path(self, target: str) -> Path:
        return self.path / ".qew" / "state" / target / "creating.json"

    def creating_intents(self, target: str) -> JsonObject:
        """Items whose forge issue this machine started creating and has not yet recorded: {item id: ISO time}.

        Local, never committed; a damaged file reads as empty (the cost is one cheap-path create).
        """
        path = self.intent_path(target)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def record_creating(self, target: str, item_id: str) -> None:
        """Write the intent before the create is sent: a crash after the forge applied it leaves this behind."""
        with _INTENTS:
            intents = self.creating_intents(target)
            intents.setdefault(item_id, now())
            path = self.intent_path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_local(path, json.dumps(intents, sort_keys=True, indent=2) + "\n")

    def clear_creating(self, target: str, item_id: str) -> None:
        with _INTENTS:
            intents = self.creating_intents(target)
            if intents.pop(item_id, None) is None:
                return
            path = self.intent_path(target)
            if intents:
                _write_local(path, json.dumps(intents, sort_keys=True, indent=2) + "\n")
            else:
                path.unlink(missing_ok=True)

    def layout_path(self, target: str) -> Path:
        return self.path / ".qew" / "state" / target / "board-layout.json"

    def read_board_layout(self, target: str) -> JsonObject | None:
        """The board structure a worker cached for one target (see Forge.board_layout); None when absent or damaged."""
        try:
            value = json.loads(self.layout_path(target).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None  # a damaged cache only costs one structure read
        return value if isinstance(value, dict) else None

    def write_board_layout(self, target: str, value: JsonObject) -> None:
        path = self.layout_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_local(path, json.dumps(value, sort_keys=True) + "\n")

    def read_page_files(self, target: str) -> dict[int, object]:
        """The cached listing pages of a target by page number; damaged files are left out."""
        found: dict[int, object] = {}
        for path in (self.path / ".qew" / "state" / target / "pages").glob("*.json"):
            try:
                found[int(path.stem)] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
        return found

    def write_page_files(self, target: str, pages: dict[int, JsonObject]) -> None:
        """Replace the cached pages with exactly `pages`."""
        directory = self.path / ".qew" / "state" / target / "pages"
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.glob("*.json"):
            if int(path.stem) not in pages:
                path.unlink(missing_ok=True)
        for number, value in pages.items():
            _write_local(directory / f"{number}.json", json.dumps(value, sort_keys=True) + "\n")

    def remove_mirror(self, item_id: str, target: str) -> None:
        """Forget one mirror file (journaled like every write, so the session commits the deletion)."""
        discard(self.mirrors_dir / item_id / f"{target}.json")
        self.state_path(item_id, target).unlink(missing_ok=True)

    def read_board(self, target: str) -> JsonObject | None:
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

    def write_view(self, name: str, value: JsonObject, overwrite: bool) -> None:
        """One file per named board view; a new name never replaces an existing file."""
        self.views_dir.mkdir(parents=True, exist_ok=True)
        _write(self.views_dir / f"{name}.json", json.dumps(value, sort_keys=True, indent=2) + "\n", exclusive=not overwrite)

    def write_board(self, target: str, value: JsonObject) -> None:
        directory = self.path / "boards"
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / f"{target}.json", json.dumps(value, sort_keys=True, indent=2) + "\n", exclusive=False)

    def new_id(self, kind: str, taken: Iterable[str] | None = None) -> str:
        return next_id(self.id_modes[kind], taken if taken is not None else self.item_ids())


@dataclass(frozen=True)
class Snapshot:
    ledger: Ledger
    items: dict[str, Item]
    folds: dict[str, Folded]
    events: dict[str, list[Event]]

    def short(self, item_id: str) -> str:
        return item_id

    @cached_property
    def derived(self) -> dict[str, tuple[State | None, tuple[str, ...]]]:
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
    mode = data.get("evidence", "strict")
    if mode not in ("strict", "lenient"):
        raise QewError(f'{path / LEDGER_FILE}: evidence must be "strict" or "lenient"', 1)
    declared = data.get("schema_version", 1)
    return Ledger(path, prefix, parse_modes(data.get("id_mode"), ("epic", "issue", "task")), declared if type(declared) is int else 1, mode == "strict")


MIRROR_IDENTITY = ("forge", "url", "kind", "number", "node_id", "issue_url", "head", "opener", "items")
JOURNAL: list[tuple[Path, str | None]] | None = None  # (path, previous text or None) while a session records


def journal_start() -> None:
    global JOURNAL
    JOURNAL = []


def journal_take() -> list[tuple[Path, str | None]]:
    global JOURNAL
    taken, JOURNAL = JOURNAL or [], None
    return taken


def journal_rollback(entries: list[tuple[Path, str | None]]) -> None:
    """Undo recorded writes, newest first: restore previous text or delete a new file."""
    for path, previous in reversed(entries):
        if previous is None:
            path.unlink(missing_ok=True)
            if path.parent.parent.name in ("events", "mirrors", "items"):  # the per-item directory the write created
                try:
                    path.parent.rmdir()
                except OSError:
                    pass  # not empty: it holds other writes
            elif path.parent.name == attachments.DIRECTORY:  # the attachments directory, then the item directory, if the write made them
                for directory in (path.parent, path.parent.parent):
                    try:
                        directory.rmdir()
                    except OSError:
                        break
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(previous, encoding="utf-8")
        ledger_aliases.note_change(path)


def discard(path: Path) -> None:
    """Delete a ledger file, journaled like every write so the session commits (or rolls back) the deletion."""
    if JOURNAL is not None:
        JOURNAL.append((path, path.read_text(encoding="utf-8") if path.exists() else None))
    path.unlink(missing_ok=True)
    if path.name == ITEM_FILE:  # an item directory left empty goes with its file (attachments keep it)
        try:
            path.parent.rmdir()
        except OSError:
            pass
    ledger_aliases.note_change(path)


def read_text_exact(path: Path) -> str:
    """A ledger file's text with its line endings exactly as stored."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_new(path: Path, text: str) -> None:
    """Create a ledger file (and its directory); journaled, and refused when the file exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, text, exclusive=True)


def write_replacing(path: Path, text: str) -> None:
    """Replace a ledger file whole; journaled so a failed command restores it."""
    _write(path, text, exclusive=False)


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
        ledger_aliases.note_change(path)
        if JOURNAL is not None:
            JOURNAL.append((path, previous))
    except OSError as error:
        raise QewError(f"cannot write {path}: {error}", 3) from None
    finally:
        if temporary.exists():
            temporary.unlink()
