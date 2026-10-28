"""A local, rebuildable cache of the ledger's item and event files.

Reading a large ledger is dominated by opening its files (about 150 microseconds each: 165,000 files take 17 to 27 s), not by
parsing them. This keeps the TEXT of every item file and every event file in `.qew/snapshot.db` (SQLite, from the standard
library; never committed) beside a stat signature of where it came from, and re-reads only what a stat says changed. The parsers
run on the cached text exactly as on a file, so the cache cannot differ from an uncached read in meaning.

An item file is trusted while its (mtime, size, inode) is what was cached; an events directory while its (mtime, inode) is.
Every qew write replaces or adds a file, which moves those. An entry younger than RACY_NS when it was cached is read again next
time (a second change in the same timestamp tick would otherwise go unseen). Event files are immutable by contract, so only a
directory change is looked for; `qew check` reads every file itself, compares, and repairs the cache. Any trouble with the
database (damage, a lock, a read-only directory) is never an error: the files are read as if there were no cache.
"""

import atexit
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from qew.errors import QewError
from qew.ledger_event import parse_event

FORMAT = "1"
RACY_NS = 2_000_000_000
READERS = 16
PARALLEL_FROM = 200  # fewer changed files than this are read in one thread
FLUSH_EVERY = 512  # baselines read before they are written to the database

Rows = list[tuple[str, str]]  # (event file name, text), sorted by name


class Unreadable(str):
    """In place of a file's text when it could not be read: the message. Never cached; the reader reports it as the file would be."""

_open: dict[str, SnapshotCache] = {}
_registry = threading.Lock()


def enabled() -> bool:
    return not os.environ.get("QEW_NO_CACHE")


def for_ledger(ledger: Path) -> SnapshotCache | None:
    """The process's cache of this ledger, or None when it is switched off or cannot be used."""
    if not enabled():
        return None
    key = str(ledger)
    with _registry:
        found = _open.get(key)
        if found is None:
            found = _open[key] = SnapshotCache(ledger)
    return None if found.broken else found


def _stamp(status: os.stat_result) -> tuple[str, int]:
    return f"{status.st_mtime_ns}:{status.st_size}:{status.st_ino}", int(status.st_mtime_ns > time.time_ns() - RACY_NS)


class SnapshotCache:
    def __init__(self, ledger: Path) -> None:
        self.ledger = ledger
        self.path = ledger / ".qew" / "snapshot.db"
        self.broken = False
        self.mismatches = 0  # entries a verifying read found different from the cache
        self._lock = threading.RLock()
        self._items: dict[str, tuple[str, int, str]] = {}  # item dir -> (signature, racy, text)
        self._homes: dict[str, tuple[str, int]] = {}  # events dir name -> (signature, racy)
        self._valid: set[str] = set()  # events dirs whose every file `parse_event` accepted when it was cached
        self._events: dict[str, Rows] = {}
        self._baselines: dict[tuple[str, str], tuple[str, int, str, str]] = {}  # (item dir, target) -> (signature, racy, stem, text)
        self._baseline_dirty: dict[tuple[str, str], tuple[str, int, str, str]] = {}  # read since the last flush
        self._loaded = False
        atexit.register(self.flush_baselines)

    # -- the database
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(exist_ok=True)
        self._exclude()
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("pragma synchronous = off")
        connection.execute("create table if not exists meta(k text primary key, v text not null)")
        row = connection.execute("select v from meta where k = 'format'").fetchone()
        if row is not None and row[0] != FORMAT:
            for table in ("items", "homes", "events"):
                connection.execute(f"drop table if exists {table}")
        connection.execute("insert or replace into meta values('format', ?)", (FORMAT,))
        connection.execute("create table if not exists items(id text primary key, sig text not null, racy integer not null, body text not null)")
        connection.execute("create table if not exists homes(name text primary key, sig text not null, racy integer not null)")
        if "ok" not in [column[1] for column in connection.execute("pragma table_info(homes)")]:
            connection.execute("alter table homes add column ok integer not null default 0")
        connection.execute("create table if not exists events(home text not null, name text not null, body text not null, primary key(home, name)) without rowid")
        connection.execute("create table if not exists baselines(home text not null, target text not null, sig text not null, racy integer not null,"
                           " stem text not null, body text not null, primary key(home, target)) without rowid")
        connection.commit()
        return connection

    def _exclude(self) -> None:
        """Keep `.qew/` out of the repository without committing anything (the same exclusion every other cache file relies on)."""
        exclude = self.ledger / ".git" / "info" / "exclude"
        if exclude.parent.is_dir():
            text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if ".qew/" not in text.split():
                exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".qew/\n", encoding="utf-8")

    def _load(self) -> None:
        if self._loaded:
            return
        for attempt in (0, 1):
            try:
                connection = self._connect()
                try:
                    self._items = {i: (s, r, b) for i, s, r, b in connection.execute("select id, sig, racy, body from items")}
                    self._homes = {}
                    self._valid = set()
                    for n, s, r, ok in connection.execute("select name, sig, racy, ok from homes"):
                        self._homes[n] = (s, r)
                        if ok:
                            self._valid.add(n)
                    self._events = {}
                    for home, name, body in connection.execute("select home, name, body from events order by home, name"):
                        self._events.setdefault(home, []).append((name, body))
                    self._baselines = {(h, t): (sig, racy, stem, body)
                                       for h, t, sig, racy, stem, body in connection.execute("select home, target, sig, racy, stem, body from baselines")}
                finally:
                    connection.close()
                self._loaded = True
                return
            except (sqlite3.Error, OSError):
                self._items, self._homes, self._events, self._valid, self._baselines = {}, {}, {}, set(), {}
                for suffix in ("", "-wal", "-shm", "-journal"):
                    try:
                        os.unlink(f"{self.path}{suffix}")
                    except OSError:
                        pass
                if attempt:
                    self.broken = True
        self._loaded = True

    def _save(self, items: dict[str, tuple[str, int, str]], gone_items: set[str], homes: dict[str, tuple[str, int]],
              events: dict[str, Rows], gone_homes: set[str]) -> None:
        """Write what changed in one transaction. A busy or damaged database only costs the next run a re-read."""
        if not (items or gone_items or homes or events or gone_homes):
            return
        try:
            connection = self._connect()
            try:
                with connection:
                    connection.executemany("delete from items where id = ?", [(i,) for i in gone_items])
                    connection.executemany("insert or replace into items values(?, ?, ?, ?)", [(i, s, r, b) for i, (s, r, b) in items.items()])
                    for home in (*gone_homes, *events):
                        connection.execute("delete from events where home = ?", (home,))
                    connection.executemany("delete from homes where name = ?", [(h,) for h in gone_homes])
                    connection.executemany("insert or replace into homes values(?, ?, ?, ?)",
                                           [(h, s, r, int(h in self._valid)) for h, (s, r) in homes.items()])
                    connection.executemany("insert or replace into events values(?, ?, ?)",
                                           [(home, name, body) for home, rows in events.items() for name, body in rows])
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            pass

    # -- items
    def items(self, items_dir: Path, read: Callable[[str], str], verify: bool = False, sharded: bool = False) -> dict[str, str]:
        """The text of every item file by directory name, reading only the files whose stat changed (all of them when `verify`)."""
        with self._lock:
            self._load()
            known = self._items
            current: dict[str, tuple[str, int]] = {}
            stale: list[str] = []
            try:
                entries = _item_entries(items_dir, sharded)
            except OSError:
                entries = []
            for entry in entries:
                try:
                    status = os.stat(os.path.join(entry.path, "item.md"))
                except OSError:
                    continue
                current[entry.name] = _stamp(status)
                row = known.get(entry.name)
                if verify or row is None or row[0] != current[entry.name][0] or row[1]:
                    stale.append(entry.name)
            fresh = _read_all(stale, read)
            changed: dict[str, tuple[str, int, str]] = {}
            for name, text in fresh.items():
                if isinstance(text, Unreadable):
                    known.pop(name, None)
                    continue
                row = known.get(name)
                if verify and row is not None and row[0] == current[name][0] and row[2] != text:
                    self.mismatches += 1
                sig, racy = current[name]
                if row is None or row[2] != text or row[0] != sig or row[1] != racy:
                    changed[name] = (sig, racy, text)
            gone = set(known) - set(current)
            for name in gone:
                known.pop(name)
            known.update(changed)
            self._save(changed, gone, {}, {}, set())
            return {name: fresh[name] if isinstance(fresh.get(name), Unreadable) else known[name][2] for name in current}

    # -- baselines
    def baseline(self, locate: Callable[[str], Path], home: str, target: str, read: Callable[[Path], tuple[str, str] | None]) -> tuple[str, str] | None:
        """The newest committed baseline (file stem, text) of one item directory and target. One stat of the target directory
        decides: while its signature is what was cached the cached text is used, otherwise the directory is read again. A pull
        that brings in another machine's baseline adds or removes a file there, so exactly those directories are re-read."""
        with self._lock:
            self._load()
            key = (home, target)
            try:
                status = os.stat(locate(home) / target)
            except OSError:
                self._baselines.pop(key, None)
                return None
            sig, racy = _stamp(status)
            row = self._baselines.get(key)
            if row is not None and row[0] == sig and not row[1]:
                return (row[2], row[3])
            found = read(locate(home) / target)
            if found is None:
                self._baselines.pop(key, None)
                return None
            self._baselines[key] = (sig, racy, found[0], found[1])
            self._baseline_dirty[key] = self._baselines[key]
            if len(self._baseline_dirty) >= FLUSH_EVERY:
                self.flush_baselines()
            return found

    def flush_baselines(self) -> None:
        """Write the baselines read since the last flush in one transaction (also run when the process exits)."""
        with self._lock:
            changed, self._baseline_dirty = self._baseline_dirty, {}
            if not changed:
                return
            try:
                connection = self._connect()
                try:
                    with connection:
                        connection.executemany("insert or replace into baselines values(?, ?, ?, ?, ?, ?)",
                                               [(h, t, sig, racy, stem, body) for (h, t), (sig, racy, stem, body) in changed.items()])
                finally:
                    connection.close()
            except (sqlite3.Error, OSError):
                pass

    # -- events
    def events(self, locate: Callable[[str], Path], homes: set[str], read: Callable[[Path], Rows],
               verify: bool = False) -> tuple[dict[str, Rows], frozenset[str]]:
        """The event files (name, text) of each wanted events directory, reading only the directories whose stat changed, and the
        directories whose files were all validated when they entered the cache (a reader may skip validating those again; a verifying
        read trusts none)."""
        with self._lock:
            self._load()
            signatures: dict[str, tuple[str, int]] = {}
            stale: list[str] = []
            for home in homes:
                try:
                    status = os.stat(locate(home))
                except OSError:
                    continue
                signatures[home] = _stamp(status)
                row = self._homes.get(home)
                if verify or row is None or row[0] != signatures[home][0] or row[1]:
                    stale.append(home)
            fresh = _read_all(stale, lambda home: read(locate(home)))
            changed_homes: dict[str, tuple[str, int]] = {}
            changed_rows: dict[str, Rows] = {}
            damaged = {home for home, rows in fresh.items() if any(isinstance(text, Unreadable) for _name, text in rows)}
            for home, rows in fresh.items():
                if home in damaged:
                    self._homes.pop(home, None)
                    self._events.pop(home, None)
                    continue
                row = self._homes.get(home)
                if verify and row is not None and row[0] == signatures[home][0] and self._events.get(home, []) != rows:
                    self.mismatches += 1
                if row is None or self._events.get(home, []) != rows or row != signatures[home]:
                    changed_homes[home] = signatures[home]
                    changed_rows[home] = rows
                    self._events[home] = rows
            self._homes.update(changed_homes)
            for home in changed_rows:
                self._valid.discard(home)
            pending = [home for home in signatures if home not in damaged and home not in self._valid and home in self._homes]
            for home in pending:  # validate once, when the text enters the cache (or when an older cache first meets it)
                if _validates(locate(home), self._events.get(home, [])):
                    self._valid.add(home)
                    changed_homes[home] = self._homes[home]
            gone = {home for home in self._homes if home not in signatures}
            for home in gone:
                self._homes.pop(home)
                self._events.pop(home, None)
                self._valid.discard(home)
            self._save({}, set(), changed_homes, changed_rows, gone)
            result = {home: fresh[home] if home in damaged else self._events.get(home, []) for home in signatures}
            trusted = frozenset() if verify else frozenset(home for home in signatures if home in self._valid and home not in damaged)
            return result, trusted


def _validates(directory: Path, rows: Rows) -> bool:
    """Whether `parse_event` accepts every file of the directory."""
    try:
        for name, text in rows:
            if isinstance(text, Unreadable):
                return False
            parse_event(name, text, f"{directory}/{name}.json")
    except QewError:
        return False
    return True


def _item_entries(items_dir: Path, sharded: bool) -> list[os.DirEntry[str]]:
    """The item directories under `items`: its entries, or in a sharded layout those of each shard."""
    top = [entry for entry in os.scandir(items_dir) if entry.is_dir()]
    if not sharded:
        return top
    return [entry for shard in top for entry in os.scandir(shard.path) if entry.is_dir()]


def _read_all[K, T](names: list[K], read: Callable[[K], T]) -> dict[K, T]:
    if len(names) > PARALLEL_FROM:
        with ThreadPoolExecutor(max_workers=READERS) as pool:
            return dict(zip(names, pool.map(read, names), strict=True))
    return {name: read(name) for name in names}
