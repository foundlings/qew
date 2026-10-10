"""`qew item attach|detach|extract`: files and subdirectories owned by an item (see attachments.py)."""

import argparse
import json
import shutil
from pathlib import Path

from qew import assignees, attach_batch, attachments, cmd_items, id_migration, ledger_config
from qew.errors import QewError
from qew.ledger_event import State, attachments_after
from qew.ledger_ids import next_id, valid_id
from qew.ledger_store import Ledger, actor


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    group = commands.add_parser("item", help="things done with one item: attach | detach | extract | rename | assign | unassign")
    verbs = group.add_subparsers(dest="item_command", required=True)
    attach = verbs.add_parser("attach", help="attach a file, or a directory with its subdirectories, to an item")
    attach.add_argument("item", nargs="?", help="item id or alias")
    attach.add_argument("source", nargs="?", help="a file or a directory on disk")
    attach.add_argument("--manifest", help="instead of ITEM SOURCE: a JSON Lines file of {item, source, name} rows, applied in one commit")
    attach.add_argument("--dry-run", action="store_true", help="say what would be attached and write nothing")
    attach.add_argument("--as", dest="name", help="the name in the item's tree (a directory's files go under it); default the file name, or the root for a directory")
    detach = verbs.add_parser("detach", help="detach a name, or a whole directory of names, from an item (the bytes stay in history)")
    detach.add_argument("item", help="item id or alias")
    detach.add_argument("name", help="a file name or a directory name in the item's tree")
    extract = verbs.add_parser("extract", help="write an item's attached tree to a directory as real files")
    extract.add_argument("item", help="item id or alias")
    extract.add_argument("dest", help="a directory that does not exist or is empty")
    assign = verbs.add_parser("assign", help="list forge logins as further assignees of an item (the claim keeps one owner)")
    assign.add_argument("item", help="item id or alias")
    assign.add_argument("logins", nargs="+", help="forge logins")
    unassign = verbs.add_parser("unassign", help="remove assignees an item lists")
    unassign.add_argument("item", help="item id or alias")
    unassign.add_argument("logins", nargs="+", help="forge logins")
    move = verbs.add_parser("rename", help="move an item to another id (default: the next free id of its kind); the old id stays as an alias")
    move.add_argument("item", help="item id or alias")
    move.add_argument("new", nargs="?", help="the new id; letters kinds accept only the next letter")
    move.add_argument("--after", help="letters kinds: treat this letter id as taken too (a letter reserved outside the ledger)")
    move.add_argument("--dry-run", action="store_true", help="say what would move and write nothing")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "item":
        return False
    ledger = cmd_items.ledger_for(ledger_config.load())
    if arguments.item_command == "attach" and arguments.manifest:
        if arguments.item or arguments.source or arguments.name:
            raise QewError("--manifest replaces ITEM, SOURCE and --as: give the manifest alone", 2)
        print(attach_manifest(ledger, Path(arguments.manifest), arguments.dry_run))
        return True
    if arguments.item_command == "attach" and not (arguments.item and arguments.source):
        raise QewError("give ITEM and SOURCE, or --manifest FILE", 2)
    item_id = ledger.resolve(arguments.item, ledger.item_ids())
    if arguments.item_command == "attach":
        print(attach(ledger, item_id, Path(arguments.source), arguments.name, arguments.dry_run))
    elif arguments.item_command == "detach":
        print(detach(ledger, item_id, arguments.name))
    elif arguments.item_command in ("assign", "unassign"):
        print(assign(ledger, item_id, arguments.logins, arguments.item_command == "assign"))
    elif arguments.item_command == "rename":
        print(rename(ledger, item_id, arguments.new, arguments.after, arguments.dry_run))
    else:
        print(extract(ledger, item_id, Path(arguments.dest)))
    return True


def attach(ledger: Ledger, item_id: str, source: Path, name: str | None, dry_run: bool = False) -> str:
    batch = attach_batch.plan(ledger, [attach_batch.Request(item_id, source, name)])
    if not dry_run:
        attach_batch.apply(ledger, batch)
    if not batch.rows:
        return f"attachments of {item_id} unchanged"
    same = f" ({batch.unchanged} unchanged)" if batch.unchanged else ""
    verb = "would attach" if dry_run else "attached"
    return f"{verb} {len(batch.rows)} file(s) to {item_id}{same}"


_MANIFEST_KEYS = ("item", "source", "name")


def attach_manifest(ledger: Ledger, manifest: Path, dry_run: bool) -> str:
    """JSON Lines of {"item", "source", optional "name"}: relative sources are relative to the manifest's directory."""
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {manifest}: {error}", 2) from None
    requests: list[attach_batch.Request] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {number}"
        try:
            row = json.loads(line)
        except ValueError as error:
            raise QewError(f"{where}: invalid JSON ({error})", 2) from None
        if not isinstance(row, dict) or set(row) - set(_MANIFEST_KEYS) or not isinstance(row.get("item"), str) or not isinstance(row.get("source"), str):
            raise QewError(f"{where}: a row is an object with `item` and `source` (and optional `name`), nothing else", 2)
        name = row.get("name")
        if name is not None and not isinstance(name, str):
            raise QewError(f"{where}: `name` must be text", 2)
        try:
            item_id = ledger.resolve(row["item"], ledger.item_ids())
        except QewError as error:
            raise QewError(f"{where}: {error}", error.exit_code) from None
        source = Path(row["source"])
        requests.append(attach_batch.Request(item_id, source if source.is_absolute() else manifest.parent / source, name, where))
    batch = attach_batch.plan(ledger, requests)
    if not dry_run:
        attach_batch.apply(ledger, batch)
    return attach_batch.summary(batch, "would attach" if dry_run else "attached")


def assign(ledger: Ledger, item_id: str, logins: list[str], adding: bool) -> str:
    """Append `assigned` / `unassigned` events for the logins whose state changes; the rest is unchanged."""
    current = set(assignees.assignees_after(ledger.load_events(item_id)))
    changed = []
    for login in dict.fromkeys(assignees.checked_login(login) for login in logins):
        if (login in current) == adding:
            continue
        ledger.append_event(item_id, "assigned" if adding else "unassigned", login=login)
        changed.append(login)
    if not changed:
        return f"assignees of {item_id} unchanged"
    return f"{'assigned' if adding else 'unassigned'} {' '.join(changed)} on {item_id}"


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
