"""`qew pr`: open a pull request for an item, mark it ready, merge only its checked head.

The pull request never carries a closing keyword: finishing work is `qew complete`,
not a forge side effect.
"""

from dataclasses import replace
import json
from typing import Callable, List, Optional

from qew import forge_registry, ledger_config, vcs
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards
from qew.errors import QewError
from qew.forge import Forge, ForgeError, PullRequestSpec, marker_for
from qew.ledger_config import Config, Mirror
from qew.ledger_event import State
from qew.ledger_store import Ledger, now

Build = Callable[[Mirror], Forge]


def register(commands) -> None:
    pr = commands.add_parser("pr", help="pull requests for items")
    sub = pr.add_subparsers(dest="pr_command", required=True)
    opened = sub.add_parser("open", help="open a draft pull request for an item")
    opened.add_argument("item", help="item id or alias")
    opened.add_argument("--target", help="mirror target (slug or unique part)")
    opened.add_argument("--head", help="pushed branch; default is the current branch")
    opened.add_argument("--base", help="base branch; default is the configured target branch")
    opened.add_argument("--ready", action="store_true", help="open ready for review instead of as a draft")
    ready = sub.add_parser("ready", help="mark a draft pull request ready for review")
    ready.add_argument("number", type=int, help="pull request number")
    ready.add_argument("--target", help="mirror target (slug or unique part)")
    merge = sub.add_parser("merge", help="merge a ready, conflict-free, green pull request at its checked head")
    merge.add_argument("number", type=int, help="pull request number")
    merge.add_argument("--target", help="mirror target (slug or unique part)")


def run(arguments) -> bool:
    if arguments.command != "pr":
        return False
    config = ledger_config.load()
    if arguments.pr_command == "open":
        print(open_pull_request(config, arguments.item, arguments.target, arguments.head, arguments.base, arguments.ready))
    elif arguments.pr_command == "ready":
        print(mark_ready(config, arguments.number, arguments.target))
    else:
        print(merge_pull_request(config, arguments.number, arguments.target))
    return True


def _mirror_for(config: Config, ledger: Ledger, slug: str) -> Mirror:
    return next(m for m in mirrors_with_boards(ledger, config) if ledger_config.mirror_slug(m) == slug)


def open_pull_request(
    config: Config,
    reference: str,
    target: Optional[str],
    head: Optional[str],
    base: Optional[str],
    ready: bool,
    build: Build = forge_registry.build,
) -> str:
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    item_id = ledger.resolve(reference, list(snapshot.items))
    state = snapshot.folds[item_id].state
    if state in (State.COMPLETED, State.CLOSED):
        raise QewError(f"{snapshot.short(item_id)} is {state.value}; reopen it before opening a pull request", 2)
    issues = [m for m in ledger.load_mirrors(item_id) if m.get("kind") == "issue"]
    if target:
        issues = [m for m in issues if target in m["_target"]]
    if len(issues) != 1:
        raise QewError(
            f"{snapshot.short(item_id)} has {len(issues)} matching issue mirrors; run `qew sync` first or pass --target", 2
        )
    issue = issues[0]
    mirror = _mirror_for(config, ledger, issue["_target"])
    item = snapshot.items[item_id]
    draft = not ready or state == State.DRAFT
    spec = PullRequestSpec(
        head=vcs.synced_branch(head),
        base=base or config.target,
        title=item.title,
        body=f"{marker_for(item_id)}\n\nDelivers {item_id} (issue #{issue['number']}).\n",
        draft=draft,
    )
    try:
        remote = build(mirror).open_pull_request(spec)
    except ForgeError as error:
        raise QewError(f"{issue['_target']}: {error.detail}", 1) from None
    ledger.write_mirror(
        item_id,
        f"{issue['_target']}-pr-{remote.number}",
        {
            "forge": mirror.forge,
            "url": mirror.repo,
            "kind": "pr",
            "draft": draft,
            "number": remote.number,
            "issue_url": remote.url,
            "head": spec.head,
            "synced_at": now(),
        },
    )
    return f"{snapshot.short(item_id)} pr #{remote.number} {remote.url}{' (draft)' if draft else ''}"


def _recorded(ledger: Ledger, number: int, target: Optional[str]):
    found = []
    for item_id in ledger.item_ids():
        for record in ledger.load_mirrors(item_id):
            if record.get("kind") == "pr" and record.get("number") == number and (not target or target in record["_target"]):
                found.append((item_id, record))
    if not found:
        raise QewError(f"pull request #{number} is not recorded in the ledger; open it with `qew pr open`", 2)
    if len(found) > 1:
        raise QewError(f"pull request #{number} exists on several targets; pass --target", 2)
    return found[0]


def _forge_for(config: Config, ledger: Ledger, record: dict, build: Build) -> Forge:
    slug = record["_target"].rsplit("-pr-", 1)[0]
    return build(_mirror_for(config, ledger, slug))


def mark_ready(config: Config, number: int, target: Optional[str], build: Build = forge_registry.build) -> str:
    ledger = ledger_for(config)
    item_id, record = _recorded(ledger, number, target)
    snapshot = ledger.snapshot()
    if snapshot.folds[item_id].state == State.DRAFT:
        raise QewError(f"{snapshot.short(item_id)} is a draft; its pull request stays a draft until a human releases it", 2)
    try:
        _forge_for(config, ledger, record, build).mark_pull_request_ready(number)
    except ForgeError as error:
        raise QewError(error.detail, 1) from None
    return f"pr #{number} ready"


def merge_pull_request(config: Config, number: int, target: Optional[str], build: Build = forge_registry.build) -> str:
    ledger = ledger_for(config)
    item_id, record = _recorded(ledger, number, target)
    snapshot = ledger.snapshot()
    if snapshot.folds[item_id].state == State.DRAFT:
        raise QewError(f"{snapshot.short(item_id)} is a draft; its pull request cannot merge before approval", 2)
    forge = _forge_for(config, ledger, record, build)
    try:
        pull = forge.read_pull_request(number)
        if pull.state != "open":
            raise QewError(f"pr #{number} is {pull.state}", 2)
        if pull.draft:
            raise QewError(f"pr #{number} is a draft; run `qew pr ready {number}`", 2)
        if pull.mergeable is not True:
            raise QewError(f"pr #{number} has conflicts or an unknown merge state", 2)
        if pull.checks not in ("passing", "none"):
            raise QewError(f"pr #{number} checks are {pull.checks}", 2)
        forge.merge_pull_request(number, pull.head_sha)
    except ForgeError as error:
        raise QewError(f"pr #{number}: {error.detail}", 1) from None
    return f"pr #{number} merged at {pull.head_sha[:12]}; record evidence and run `qew complete {snapshot.short(item_id)}`"
