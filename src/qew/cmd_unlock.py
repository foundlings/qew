"""`qew unlock --stale`: remove the repository locks a crashed git left behind, only on a human's quoted approval.

git's `index.lock` and ref locks are sentinel files, not kernel locks, so a dead git leaves them. qew never removes one
on its own. This command removes a lock inside the ledger's `.git` only when it is old (60 s), no process has it open,
and no qew process holds a ledger lock; every removal is appended to the ledger's `.qew/unlock.log` with who, when,
the approving words and each file's age.
"""

import json
import os
from pathlib import Path
import time
from typing import List

from qew import filelock, ledger_config, ledger_store
from qew.errors import QewError
from qew.ledger_repo import STALE_AFTER_S, lock_in_use
from qew.requester import require_quote

QEW_LOCKS = ("lock", "push.lock", "remote.lock", "sync.lock")


def register(commands) -> None:
    unlock = commands.add_parser("unlock", help="remove a stale repository lock a crashed git left, with a human's quoted approval")
    unlock.add_argument("--stale", action="store_true", required=True, help="remove only locks that are old and open in no process")
    unlock.add_argument("--quote", help="the approving human's words, \"Name: 'their words'\" (recorded in .qew/unlock.log)")
    unlock.add_argument("--dry-run", action="store_true", help="list the stale locks without removing them")
    unlock.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command != "unlock":
        return False
    quote = require_quote(arguments.quote)
    config = ledger_config.load()
    removed = unlock(config.ledger_path, quote, arguments.dry_run)
    if arguments.json:
        print(json.dumps({"schema_version": 1, "dry_run": arguments.dry_run, "removed": removed}))
    elif removed:
        verb = "would remove" if arguments.dry_run else "removed"
        print("\n".join(f"{verb} {row['path']} ({row['age_s']}s old)" for row in removed))
    else:
        print("no stale lock found")
    return True


def candidates(ledger_path: Path) -> List[Path]:
    git_dir = ledger_path / ".git"
    if not git_dir.is_dir():
        raise QewError(f"{ledger_path} has no .git directory; qew only clears git's own locks there", 2)
    found = []
    for directory, _, files in os.walk(git_dir):
        found.extend(Path(directory) / name for name in files if name.endswith(".lock"))
    return sorted(found)


def unlock(ledger_path: Path, quote: str, dry_run: bool) -> List[dict]:
    for name in QEW_LOCKS:
        lock = ledger_path / ".qew" / name
        if filelock.is_held(lock):
            holder = filelock.read_holder(lock) or {}
            raise QewError(f"a qew process holds the ledger lock `{name}` ({holder.get('who', '?')}'s `{holder.get('verb', '?')}`, "
                           f"pid {holder.get('pid', '?')}); nothing was removed", 3)
    now = time.time()
    chosen = []
    for path in candidates(ledger_path):
        age = max(0.0, now - path.stat().st_mtime)
        if age < STALE_AFTER_S:
            raise QewError(f"{path} is only {age:.0f}s old, so it is not stale (a running git may own it); nothing was removed", 3)
        if lock_in_use(str(path)) is not False:
            reason = "is in use by a running process" if lock_in_use(str(path)) else "cannot be shown unused (lsof is missing)"
            raise QewError(f"{path} {reason}; nothing was removed", 3)
        chosen.append({"path": str(path), "age_s": int(age)})
    if dry_run or not chosen:
        return chosen
    log = ledger_path / ".qew" / "unlock.log"
    log.parent.mkdir(exist_ok=True)
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "actor": ledger_store.actor(), "quote": quote, "removed": chosen}
    with open(log, "a", encoding="utf-8") as handle:  # written before the removal: a removal is never unrecorded
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    for row in chosen:
        Path(row["path"]).unlink(missing_ok=True)
    return chosen
