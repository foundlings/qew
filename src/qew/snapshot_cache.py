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

import os
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FORMAT = "1"
RACY_NS = 2_000_000_000
READERS = 16
PARALLEL_FROM = 200  # fewer changed files than this are read in one thread

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
        self._events: dict[str, Rows] = {}
        self._loaded = False

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
        connection.execute("create table if not exists events(home text not null, name text not null, body text not null, primary key(home, name)) without rowid")
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
                    self._homes = {n: (s, r) for n, s, r in connection.execute("select name, sig, racy from homes")}
                    self._events = {}
                    for home, name, body in connection.execute("select home, name, body from events order by home, name"):
                        self._events.setdefault(home, []).append((name, body))
                finally:
                    connection.close()
                self._loaded = True
                return
            except (sqlite3.Error, OSError):
                self._items, self._homes, self._events = {}, {}, {}
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
                    connection.executemany("insert or replace into homes values(?, ?, ?)", [(h, s, r) for h, (s, r) in homes.items()])
                    connection.executemany("insert or replace into events values(?, ?, ?)",
                                           [(home, name, body) for home, rows in events.items() for name, body in rows])
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            pass

    # -- items
    def items(self, items_dir: Path, read: Callable[[str], str], verify: bool = False) -> dict[str, str]:
        """The text of every item file by directory name, reading only the files whose stat changed (all of them when `verify`)."""
        with self._lock:
            self._load()
            known = self._items
            current: dict[str, tuple[str, int]] = {}
            stale: list[str] = []
            try:
                entries = [entry for entry in os.scandir(items_dir) if entry.is_dir()]
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

    # -- events
    def events(self, events_dir: Path, homes: set[str], read: Callable[[Path], Rows], verify: bool = False) -> dict[str, Rows]:
        """The event files (name, text) of each wanted events directory, reading only the directories whose stat changed."""
        with self._lock:
            self._load()
            signatures: dict[str, tuple[str, int]] = {}
            stale: list[str] = []
            for home in homes:
                try:
                    status = os.stat(events_dir / home)
                except OSError:
                    continue
                signatures[home] = _stamp(status)
                row = self._homes.get(home)
                if verify or row is None or row[0] != signatures[home][0] or row[1]:
                    stale.append(home)
            fresh = _read_all(stale, lambda home: read(events_dir / home))
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
            gone = {home for home in self._homes if home not in signatures}
            for home in gone:
                self._homes.pop(home)
                self._events.pop(home, None)
            self._save({}, set(), changed_homes, changed_rows, gone)
            return {home: fresh[home] if home in damaged else self._events.get(home, []) for home in signatures}


def _read_all[T](names: list[str], read: Callable[[str], T]) -> dict[str, T]:
    if len(names) > PARALLEL_FROM:
        with ThreadPoolExecutor(max_workers=READERS) as pool:
            return dict(zip(names, pool.map(read, names), strict=True))
    return {name: read(name) for name in names}
