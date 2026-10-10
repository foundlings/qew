"""`qew item attach|detach|extract`: files and subdirectories owned by an item (see attachments.py)."""

import argparse
import shutil
import sys
from pathlib import Path

from qew import attachments, cmd_items, id_migration, ledger_config
from qew.errors import QewError
from qew.ledger_event import State, attachments_after
from qew.ledger_ids import next_id, valid_id
from qew.ledger_store import Ledger, actor


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    group = commands.add_parser("item", help="things done with one item: attach | detach | extract | rename")
    verbs = group.add_subparsers(dest="item_command", required=True)
    attach = verbs.add_parser("attach", help="attach a file, or a directory with its subdirectories, to an item")
    attach.add_argument("item", help="item id or alias")
    attach.add_argument("source", help="a file or a directory on disk")
    attach.add_argument("--as", dest="name", help="the name in the item's tree (a directory's files go under it); default the file name, or the root for a directory")
    detach = verbs.add_parser("detach", help="detach a name, or a whole directory of names, from an item (the bytes stay in history)")
    detach.add_argument("item", help="item id or alias")
    detach.add_argument("name", help="a file name or a directory name in the item's tree")
    extract = verbs.add_parser("extract", help="write an item's attached tree to a directory as real files")
    extract.add_argument("item", help="item id or alias")
    extract.add_argument("dest", help="a directory that does not exist or is empty")
    move = verbs.add_parser("rename", help="move an item to another id (default: the next free id of its kind); the old id stays as an alias")
    move.add_argument("item", help="item id or alias")
    move.add_argument("new", nargs="?", help="the new id; letters kinds accept only the next letter")
    move.add_argument("--after", help="letters kinds: treat this letter id as taken too (a letter reserved outside the ledger)")
    move.add_argument("--dry-run", action="store_true", help="say what would move and write nothing")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "item":
        return False
    ledger = cmd_items.ledger_for(ledger_config.load())
    item_id = ledger.resolve(arguments.item, ledger.item_ids())
    if arguments.item_command == "attach":
        print(attach(ledger, item_id, Path(arguments.source), arguments.name))
    elif arguments.item_command == "detach":
        print(detach(ledger, item_id, arguments.name))
    elif arguments.item_command == "rename":
        print(rename(ledger, item_id, arguments.new, arguments.after, arguments.dry_run))
    else:
        print(extract(ledger, item_id, Path(arguments.dest)))
    return True


def _files(source: Path, name: str | None) -> list[tuple[str, Path]]:
    """(attachment name, file) for a file or for every file under a directory; symlinks and special files are refused."""
    if name is not None:
        attachments.checked_path(name)
    if source.is_symlink():
        raise QewError(f"{source} is a symlink; name the real file", 2)
    if source.is_file():
        return [(attachments.checked_path(source.name if name is None else name), source)]
    if not source.is_dir():
        raise QewError(f"{source} is not a file or a directory", 2)
    found: list[tuple[str, Path]] = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise QewError(f"{path} is a symlink; attachments are regular files only", 2)
        if path.is_dir():
            continue
        if not path.is_file():
            raise QewError(f"{path} is not a regular file", 2)
        relative = path.relative_to(source).as_posix()
        found.append((attachments.checked_path(f"{name}/{relative}" if name is not None else relative), path))
    if not found:
        raise QewError(f"{source} holds no files", 2)
    return found


def attach(ledger: Ledger, item_id: str, source: Path, name: str | None) -> str:
    planned = _files(source, name)
    if len({path for path, _ in planned}) != len(planned):
        raise QewError("two files would have the same attachment name", 2)
    limit = attachments.hard_limit()
    hashed: list[tuple[str, Path, str, int]] = []
    for path, file in planned:
        try:
            digest, size = attachments.hash_file(file)
        except OSError as error:
            raise QewError(f"cannot read {file}: {error}", 2) from None
        if size > limit:
            raise QewError(f"{file} is {size} bytes; the limit is {limit} (QEW_ATTACH_MAX_BYTES)", 2)
        if size > attachments.SOFT_FILE_BYTES:
            print(f"warning: {file} is {size // (1024 * 1024)} MiB; large files bloat the ledger repository", file=sys.stderr)
        hashed.append((path, file, digest, size))
    current = attachments_after(ledger.load_events(item_id))
    changed = [row for row in hashed if current.get(row[0]) is None or current[row[0]].digest != row[2]]
    names = {path for path, *_ in hashed}
    for path, *_ in changed:
        clash = attachments.clashes((set(current) - names) | names, path)
        if clash:
            raise QewError(f"`{path}` cannot be both a file and a directory (clashes with {', '.join(clash)}); detach one first", 2)
    stored = {row.digest: row.size for row in current.values()}
    stored.update({digest: size for _, _, digest, size in changed})
    if sum(stored.values()) > attachments.ITEM_BYTES:
        raise QewError(f"{item_id} would hold {sum(stored.values())} bytes of attachments; the limit is {attachments.ITEM_BYTES}", 2)
    for path, file, digest, size in changed:
        blob = attachments.blob_name(digest, attachments.extension_of(file.name))
        ledger.store_blob(item_id, file, blob)
        ledger.append_event(item_id, "attached", path=path, hash=digest, size=size, blob=blob)
    if not changed:
        return f"attachments of {item_id} unchanged"
    same = len(hashed) - len(changed)
    return f"attached {len(changed)} file(s) to {item_id}" + (f" ({same} unchanged)" if same else "")


def detach(ledger: Ledger, item_id: str, name: str) -> str:
    attachments.checked_path(name)
    current = attachments_after(ledger.load_events(item_id))
    found = sorted(path for path in current if path == name or path.startswith(name + "/"))
    if not found:
        raise QewError(f"nothing is attached to {item_id} under `{name}`", 2)
    for path in found:
        ledger.append_event(item_id, "detached", path=path)
    return f"detached {len(found)} file(s) from {item_id}"


def extract(ledger: Ledger, item_id: str, dest: Path) -> str:
    current = attachments_after(ledger.load_events(item_id))
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise QewError(f"{dest} exists and is not an empty directory", 2)
    sources: list[tuple[Path, Path]] = []
    for path, row in sorted(current.items()):
        blob = ledger.find_blob(item_id, row.blob)
        if blob is None:
            raise QewError(f"{item_id}: the stored file for `{path}` is missing ({row.blob}); run `qew check`", 1)
        if attachments.hash_file(blob)[0] != row.digest:
            raise QewError(f"{item_id}: the stored file for `{path}` does not match its hash; run `qew check`", 1)
        sources.append((blob, dest / path))
    for blob, target in sources:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blob, target)
    return f"extracted {len(sources)} file(s) of {item_id} to {dest}"


def rename(ledger: Ledger, item_id: str, new: str | None, after: str | None, dry_run: bool) -> str:
    snapshot = ledger.snapshot()
    folded = snapshot.folds[item_id]
    holder = actor()
    if folded.state == State.CLAIMED and folded.owner != holder:
        raise QewError(f"{item_id} is claimed by {folded.owner}; ids are not changed under another login's claim "
                       f"(ask them to release it, or run as that login)", 3)
    kind = snapshot.items[item_id].kind.value
    mode = ledger.id_modes[kind]
    taken = {*ledger.item_ids(), *ledger.aliases().by_alias}
    if after:
        if mode != "letters":
            raise QewError(f"--after applies to letters kinds; {kind} ids are {mode}", 2)
        taken.add(after)
    if mode == "letters":
        expected = next_id(mode, taken - {item_id})
        if new is not None and new != expected:
            raise QewError(f"{kind} ids run in strict letter order, so the next is {expected}, not {new} "
                           f"(leave the id out, or use --after for a letter reserved outside the ledger)", 2)
        new = expected
    elif new is None:
        new = next_id(mode, taken)
    if not valid_id(new):
        raise QewError(f"`{new}` is not a valid id", 2)
    if new.lower() in {name.lower() for name in taken}:
        raise QewError(f"{new} is already an item id or an alias", 2)
    report = id_migration.rename(ledger, [(item_id, new)], dry_run)
    verb = "would rename" if dry_run else "renamed"
    return f"{verb} {item_id} -> {new} ({report.rewritten} files followed; {item_id} stays as an alias)"
