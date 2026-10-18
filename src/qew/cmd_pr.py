"""`qew pr`: open a pull request for an item, tick its checklist, mark it ready, merge only its checked head.

The pull request never carries a closing keyword: finishing work is `qew complete`
(or `qew land`), not a forge side effect. The ledger, not the forge, says who opened a
pull request and which items it covers.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import re
import sys
from collections.abc import Callable, Iterator

from qew import sync_plan, branch_cleanup, pr_adopt, forge_registry, ledger_config, pr_body, pr_checklist, pr_gate, pr_record, untrusted_text, vcs
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards
from qew.errors import QewError
from qew.forge import Forge, ForgeError, PullRequestSpec, RemotePullRequest, Unsupported
from qew.ledger_config import Config, Mirror
from qew.ledger_event import State
from qew.ledger_ids import valid_id
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot, actor, now
from qew.mirror_types import MirrorRecord
from qew.pr_record import PrRecord
from qew.pr_verify import Outcome, apply_and_verify

Build = Callable[[Mirror], Forge]
SCHEMA_VERSION = 1
LOGIN_VARIABLE = "QEW_FORGE_LOGIN"  # the forge login a pull request is assigned to, when it differs from the actor
MERGE_RECHECK_SECONDS = 1.0
_TARGET_HELP = "mirror target (slug or unique part)"
_REASON_HELP = "why: required with --admin and --other-author"


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    pr = commands.add_parser("pr", help="pull requests for items")
    sub = pr.add_subparsers(dest="pr_command", required=True)
    opened = sub.add_parser("open", help="open a draft pull request for an item")
    opened.add_argument("item", help="item id or alias")
    opened.add_argument("--target", help=_TARGET_HELP)
    opened.add_argument("--head", help="pushed branch; default is the current branch")
    opened.add_argument("--base", help="base branch; default is the configured target branch")
    opened.add_argument("--ready", action="store_true", help="open ready for review instead of as a draft (never for a draft item)")
    opened.add_argument("--what", help="the What section; default is the item body")
    opened.add_argument("--why", help="the Why section")
    opened.add_argument("--testing", help="the Testing section")
    opened.add_argument("--body-file", help="append this file to the body, e.g. a `## Progress` checklist of `- [ ] text <!-- pr-progress:ID -->` lines")
    opened.add_argument("--adopt-legacy", type=int, metavar="NUMBER", help="take over this unmarked pull request qew never recorded (from before qew) on --head: it keeps its number and head (needs --reason)")
    opened.add_argument("--reason", help="why: required with --adopt-legacy")
    opened.add_argument("--json", action="store_true", help="machine-readable output")
    check = sub.add_parser("check", help="tick progress checklist items in a pull request body")
    check.add_argument("number", type=int, help="pull request number")
    check.add_argument("ids", nargs="*", help="checklist items to tick: marker id, position, or @position")
    check.add_argument("--all", action="store_true", help="tick every open item")
    check.add_argument("--list", action="store_true", help="show the items and whether they are ticked; write nothing")
    check.add_argument("--drop", metavar="ID", help="remove this item's box (the PR will not finish it); every other tick stays (needs --reason)")
    check.add_argument("--reason", help="why: required with --drop")
    check.add_argument("--add", action="append", metavar="ITEM", help="add an unticked Progress entry for this item (its title) after the last box; repeat for several")
    check.add_argument("--force", action="store_true", help="with --drop: also drop a box that is already ticked")
    check.add_argument("--on-behalf-of", metavar="ACTOR", help="act as this actor would: allowed only where ACTOR could (its opener, or the holder of its claims)")
    check.add_argument("--target", help=_TARGET_HELP)
    ready = sub.add_parser("ready", help="mark a draft pull request ready for review")
    ready.add_argument("number", type=int, help="pull request number")
    ready.add_argument("--target", help=_TARGET_HELP)
    ready.add_argument("--json", action="store_true", help="machine-readable output")
    drafted = sub.add_parser("draft", help="move a ready pull request back to draft (the inverse of ready)")
    drafted.add_argument("number", type=int, help="pull request number")
    drafted.add_argument("--target", help=_TARGET_HELP)
    drafted.add_argument("--json", action="store_true", help="machine-readable output")
    bodied = sub.add_parser("body", help="replace a pull request's body from a file, keeping its Progress boxes and the ticks already earned")
    bodied.add_argument("number", type=int, help="pull request number")
    bodied.add_argument("--body-file", required=True, metavar="FILE", help="the new body; it must keep every pr-progress box and tick none")
    bodied.add_argument("--on-behalf-of", metavar="ACTOR", help="act as this actor would: allowed only where ACTOR could (its opener, or the holder of its claims)")
    bodied.add_argument("--target", help=_TARGET_HELP)
    bodied.add_argument("--json", action="store_true", help="machine-readable output")
    labelled = sub.add_parser("label", help="add and remove labels on a pull request, keeping the others")
    labelled.add_argument("number", type=int, help="pull request number")
    labelled.add_argument("--add", action="append", default=[], metavar="LABEL", help="label to add, repeatable")
    labelled.add_argument("--remove", action="append", default=[], metavar="LABEL", help="label to remove, repeatable")
    labelled.add_argument("--on-behalf-of", metavar="ACTOR", help="label as this actor would: allowed only where ACTOR could label the pull request (its opener, or the holder of its claims)")
    labelled.add_argument("--target", help=_TARGET_HELP)
    labelled.add_argument("--json", action="store_true", help="machine-readable output")
    closed = sub.add_parser("close", help="close an unmerged pull request without merging it")
    closed.add_argument("number", type=int, help="pull request number")
    closed.add_argument("--target", help=_TARGET_HELP)
    closed.add_argument("--item", metavar="ID", help="a pull request qew never recorded (a legacy draft): adopt it onto this item, which carries its remainder, then close it")
    closed.add_argument("--reason", help="with --item: why it is closed (kept in the ledger)")
    closed.add_argument("--json", action="store_true", help="machine-readable output")
    synced = sub.add_parser("sync", help="set an open pull request's title from its item's current title, or with --items re-derive the items it covers")
    synced.add_argument("number", type=int, help="pull request number")
    synced.add_argument("--items", action="store_true", help="instead of the title, re-derive the covered items: recorded + the root's open descendants + items with a Progress box")
    synced.add_argument("--target", help=_TARGET_HELP)
    synced.add_argument("--json", action="store_true", help="machine-readable output")
    merge = sub.add_parser("merge", help="merge a ready, conflict-free, green pull request at its checked head")
    merge.add_argument("number", type=int, help="pull request number")
    merge.add_argument("--target", help=_TARGET_HELP)
    merge.add_argument("--auto", action="store_true", help="merge once the checks pass, only where the base branch requires checks")
    merge.add_argument("--admin", action="store_true", help="merge past pending or failing checks only (needs --reason)")
    merge.add_argument("--other-author", action="store_true", help="merge a pull request someone else opened (needs --reason)")
    merge.add_argument("--reason", help=_REASON_HELP)
    merge.add_argument("--check", action="store_true", help="run every refusal the merge would apply, then stop: merges and writes nothing, exits as the merge would")
    merge.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "pr":
        return False
    config = ledger_config.load()
    command = arguments.pr_command
    if command == "open":
        options = OpenOptions(
            arguments.target, arguments.head, arguments.base, arguments.ready,
            arguments.what, arguments.why, arguments.testing, arguments.body_file, arguments.adopt_legacy, arguments.reason,
        )
        opened = open_pull_request(config, arguments.item, options)
        print(opened.render(arguments.json))
        if opened.unassigned:
            print(
                f"qew: pr #{opened.number} was not assigned to {opened.unassigned} (the forge refused); "
                f"the opener is recorded in the ledger; set [forge] login in .qew/config.toml (or {LOGIN_VARIABLE}) to your forge login",
                file=sys.stderr,
            )
    elif command == "check":
        print(check_pull_request(config, arguments.number, arguments.ids, arguments.all, arguments.list, arguments.target,
                                 drop=arguments.drop, reason=arguments.reason, force=arguments.force, add=arguments.add, on_behalf_of=arguments.on_behalf_of))
    elif command == "ready":
        print(mark_ready(config, arguments.number, arguments.target).render(arguments.json))
    elif command == "body":
        print(replace_body(config, arguments.number, arguments.body_file, arguments.target, on_behalf_of=arguments.on_behalf_of).render(arguments.json))
    elif command == "draft":
        print(mark_draft(config, arguments.number, arguments.target).render(arguments.json))
    elif command == "close":
        print(close_pull_request(config, arguments.number, arguments.target, item=arguments.item, reason=arguments.reason).render(arguments.json))
    elif command == "label":
        print(label_pull_request(config, arguments.number, arguments.add, arguments.remove, arguments.target, on_behalf_of=arguments.on_behalf_of).render(arguments.json))
    elif command == "sync":
        if arguments.items:
            print(sync_items(config, arguments.number, arguments.target))
        else:
            print(sync_title(config, arguments.number, arguments.target).render(arguments.json))
    else:
        merge_options = MergeOptions(arguments.auto, arguments.admin, arguments.other_author, arguments.reason, arguments.check)
        if arguments.check:
            return _report_check(arguments, merge_check(config, arguments.number, arguments.target, merge_options))
        print(merge_pull_request(config, arguments.number, arguments.target, merge_options).render(arguments.json))
    return True


def _report_check(arguments: argparse.Namespace, refusals: list[QewError]) -> bool:
    """Print what `pr merge --check` found and exit with the code the merge would exit with (the first refusal's)."""
    if arguments.json:
        rows = [{"message": str(r), "exit_code": r.exit_code} for r in refusals]
        print(json.dumps({"number": arguments.number, "mergeable": not refusals, "refusals": rows, "schema_version": SCHEMA_VERSION}, sort_keys=True))
    elif not refusals:
        print(f"pr #{arguments.number}: qew merge preflight passed (ledger and forge refusals only); nothing was merged")
    if refusals:
        if not arguments.json:
            for refusal in refusals[1:]:
                print(f"qew: {refusal}", file=sys.stderr)
        raise QewError(str(refusals[0]), refusals[0].exit_code) if not arguments.json else SystemExit(refusals[0].exit_code)
    return True


# -- shared plumbing ---------------------------------------------------------------

@contextmanager
def forge_errors(target: str) -> Iterator[None]:
    """A forge failure is one stderr line naming the target, exit 1; never a traceback."""
    try:
        yield
    except ForgeError as error:
        raise QewError(f"{target}: {error.detail}", 1) from None


def mirror_for(config: Config, ledger: Ledger, slug: str) -> Mirror:
    for mirror in mirrors_with_boards(ledger, config):
        if ledger_config.mirror_slug(mirror) == slug:
            return mirror
    raise QewError(f"target {slug} is not configured in .qew/config.toml", 2)


def _build(build: Build | None, mirror: Mirror) -> Forge:
    return (build or forge_registry.build)(mirror)


@dataclass(frozen=True)
class Loaded:
    """A recorded pull request with the ledger facts every command needs, and its forge."""

    snapshot: Snapshot
    record: PrRecord
    states: dict[str, State]
    forge: Forge
    note: str  # printed before the result when an override was used


def load(
    config: Config, number: int, target: str | None, build: Build | None,
    reason: str = "", merging: bool = False, owned: bool = True, on_behalf_of: str | None = None,
) -> Loaded:
    """The recorded pull request, after the ownership rule (exit 3) and a readable-history check (exit 4).

    A read-only command passes `owned=False`: anyone may look. `on_behalf_of` judges the rule for that actor instead of the caller.
    """
    pr_record.positive_number(number)
    ledger = ledger_for(config)
    record = pr_record.find(ledger, number, target)
    snapshot = ledger.snapshot()
    states = pr_record.known_states(snapshot, record)
    note = pr_record.require_owner(snapshot, record, states, on_behalf_of or actor(), reason, merging) if owned else ""
    forge = _build(build, mirror_for(config, ledger, record.target))
    return Loaded(snapshot, record, states, forge, note)


def refuse_held(loaded: Loaded, verb: str) -> None:
    held = pr_record.drafts(loaded.states)
    if held:
        raise QewError(
            f"pr #{loaded.record.number} is held: {' '.join(held)} is a draft, so it cannot {verb} "
            f"until a human releases it (`qew draft approve {' '.join(held)} --quote ...`)",
            2,
        )


def refuse_finished(number: int, pull: RemotePullRequest, verb: str) -> None:
    if pull.state != "open":
        raise QewError(f"pr #{number} is {pull.state}; it cannot {verb}", 2)


# -- open --------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenOptions:
    target: str | None = None
    head: str | None = None
    base: str | None = None
    ready: bool = False
    what: str | None = None
    why: str | None = None
    testing: str | None = None
    body_file: str | None = None
    adopt_legacy: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Opened:
    item: str
    number: int
    url: str
    draft: bool
    unassigned: str = ""  # the login the forge would not assign, "" when assigned
    adopted: bool = False  # the pull request already existed on the forge; only the ledger record was written

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps(
                {"item": self.item, "number": self.number, "url": self.url, "draft": self.draft, "schema_version": SCHEMA_VERSION},
                sort_keys=True,
            )
        return f"{self.item} pr #{self.number} {self.url}{' (draft)' if self.draft else ''}{' adopted' if self.adopted else ''}"


def _body_file(path: str | None) -> str:
    if path is None:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read --body-file {path}: {error}", 2) from None


def _refuse_other_items_branch(snapshot: Snapshot, head: str, covered: list[str]) -> None:
    """A branch named for another item than the one being opened belongs to that other item."""
    tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", head) if len(t) >= 6}
    named = [i for i in snapshot.items if i.lower() in tokens and valid_id(i)]
    if named and not set(named) & set(covered):
        raise QewError(
            f"branch {head} names {' '.join(named)} but the pull request is for {covered[0]}; "
            "open it for that item or pass a different --head",
            2,
        )


def _ancestor_issues(config: Config, ledger: Ledger, snapshot: Snapshot, item_id: str, target: str | None) -> list[MirrorRecord]:
    """For each mirror that does not select the item, the issue record of its nearest ancestor that has one on that target."""
    found: list[MirrorRecord] = []
    for mirror in mirrors_with_boards(ledger, config):
        slug = ledger_config.mirror_slug(mirror)
        if (target and target not in slug) or sync_plan.selects(snapshot, item_id, mirror.select):
            continue  # a selected item gets its own issue from `qew sync`
        seen, cursor = {item_id}, snapshot.items[item_id].parent
        while cursor and cursor not in seen and cursor in snapshot.items:
            seen.add(cursor)
            record = next((m for m in ledger.load_mirrors(cursor) if m.get("kind") == "issue" and m["_target"] == slug), None)
            if record is not None:
                found.append(record)
                break
            cursor = snapshot.items[cursor].parent
    return found


def open_pull_request(config: Config, reference: str, options: OpenOptions = OpenOptions(), build: Build | None = None) -> Opened:
    extra = _body_file(options.body_file)
    if options.adopt_legacy is not None:
        pr_record.positive_number(options.adopt_legacy)
        if not (options.reason or "").strip():
            raise QewError("--adopt-legacy needs --reason TEXT: say who assigned the pull request to this item and why", 2)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    item_id = ledger.resolve(reference, list(snapshot.items))
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    if folded.state in (State.COMPLETED, State.CLOSED):
        raise QewError(f"{item_id} is {folded.state.value}; reopen it before opening a pull request", 2)
    covered = pr_record.covered_by(snapshot, item_id)
    held = [i for i in covered if snapshot.folds[i].state == State.DRAFT]
    if options.ready and held:
        raise QewError(
            f"{item_id} cannot open ready: {' '.join(held)} is a draft, and a pull request over a draft is always a draft", 2
        )
    if folded.state == State.APPROVED:
        raise QewError(f"{item_id} is unclaimed; run `qew start {item_id}` before opening its pull request", 2)
    if folded.state == State.CLAIMED and folded.owner != actor():
        raise QewError(f"{item_id} is claimed by {folded.owner}, not {actor()}; its pull request is theirs to open", 2)
    issues = [m for m in ledger.load_mirrors(item_id) if m.get("kind") == "issue"]
    if options.target:
        issues = [m for m in issues if options.target in m["_target"]]
    if not issues:  # an item no mirror selects (a task under epics-and-issues mirrors) has no issue: its nearest mirrored ancestor's stands in
        issues = _ancestor_issues(config, ledger, snapshot, item_id, options.target)
    if len(issues) != 1:
        raise QewError(f"{item_id} has {len(issues)} matching issue mirrors; run `qew sync` first or pass --target", 2)
    issue = issues[0]
    head = vcs.synced_branch(options.head)
    _refuse_other_items_branch(snapshot, head, covered)
    what = options.what if options.what is not None else item.body
    body = pr_body.build_body(item_id, issue["number"], what, options.why, options.testing, extra)
    pr_body.refuse_closing_references(body, "the pull request body")
    mirror = mirror_for(config, ledger, issue["_target"])
    login = os.environ.get(LOGIN_VARIABLE) or sync_plan.assignee_of(mirror.options.get("assignees"), actor()) or actor()
    spec = PullRequestSpec(head, options.base or config.target, item.title, body, not options.ready, login)
    forge = _build(build, mirror)
    base = options.base or config.target
    with forge_errors(issue["_target"]):
        remote = pr_adopt.find_adoptable(ledger, issue["_target"], forge, head, base, item_id, options.adopt_legacy)
        adopted = remote is not None
        if remote is None:
            remote = forge.open_pull_request(spec)
    ledger.write_mirror(
        item_id,
        f"{issue['_target']}-pr-{remote.number}",
        {
            "forge": mirror.forge,
            "url": mirror.repo,
            "kind": "pr",
            "draft": remote.draft,
            "state": "open",
            "number": remote.number,
            "pr_url": remote.url,
            "head": head,
            "opener": actor(),
            "items": covered,
            "synced_at": now(),
        },
    )
    if options.adopt_legacy is not None:
        ledger.append_event(item_id, "commented", text=f"adopted legacy pull request #{remote.number}: {(options.reason or '').strip()}", author=actor())
    return Opened(item_id, remote.number, remote.url, remote.draft, "" if remote.assignee == login else login, adopted)


# -- check -------------------------------------------------------------------------

def _describe(box: pr_checklist.Box) -> str:
    return f"[{'x' if box.ticked else ' '}] {box.address} {box.text}"


def check_pull_request(
    config: Config, number: int, names: list[str], every: bool, listing: bool, target: str | None,
    build: Build | None = None, drop: str | None = None, reason: str | None = None, force: bool = False,
    add: list[str] | None = None, on_behalf_of: str | None = None,
) -> str:
    if add:
        if names or every or listing or drop is not None or reason or force:
            raise QewError("--add stands alone: it cannot be combined with ticking, --all, --list, --drop, --reason or --force", 2)
        return _add_items(config, number, add, target, build, on_behalf_of)
    if drop is not None:
        if names or every or listing:
            raise QewError("--drop stands alone: it cannot be combined with ticking, --all or --list", 2)
        if not (reason or "").strip():
            raise QewError("--drop needs --reason TEXT: say why the item will not be finished", 2)
        return _drop_item(config, number, drop, reason or "", force, target, build, on_behalf_of)
    if reason or force:
        raise QewError("--reason and --force belong to --drop", 2)
    chosen = [bool(names), every, listing]
    if sum(chosen) != 1:
        raise QewError("name checklist items, or pass exactly one of --all and --list", 2)
    loaded = load(config, number, target, build, owned=not listing, on_behalf_of=on_behalf_of)
    with forge_errors(loaded.record.target):
        first = loaded.forge.read_pull_request(number)
    refuse_finished(number, first, "have its checklist edited")
    items = pr_checklist.boxes(first.body)
    if listing:
        return "\n".join(_describe(b) for b in items) or f"pr #{number} has no checklist"
    targets = [b for b in items if not b.ticked] if every else pr_checklist.resolve(items, names)
    targets = [b for b in targets if not b.ticked]
    if not targets:
        return f"pr #{number} checked: nothing to tick"
    edited = pr_checklist.tick(first.body, targets)
    _write_body(loaded, number, first, edited)
    return f"pr #{number} checked: {' '.join(b.address for b in targets)}"


def _write_body(loaded: Loaded, number: int, first: RemotePullRequest, edited: str) -> None:
    """Write `edited` only if the body is still what `first` read, and succeed only once the forge shows it."""
    with forge_errors(loaded.record.target):
        latest = loaded.forge.read_pull_request(number)
        if latest.body != first.body:
            raise QewError(f"pr #{number} body changed while it was being edited; nothing written, run again", 3)
        written = loaded.forge.update_pull_request_body(number, edited)
    if written.body != edited:
        raise QewError(f"pr #{number} body did not take the edit; the forge still shows the old checklist", 3)


def _entry_text(snapshot: Snapshot, item_id: str) -> str:
    title = " ".join(snapshot.items[item_id].title.split())
    if "<!--" in title or "-->" in title:
        raise QewError(f"{item_id}: the title carries a comment marker; rename the item first", 2)
    pr_body.refuse_closing_references(title, f"the title of {item_id}")
    return title


def _add_items(config: Config, number: int, references: list[str], target: str | None, build: Build | None, on_behalf_of: str | None) -> str:
    """Add an unticked Progress entry for each item (named by its title), keep every other byte, and note it on the covered items."""
    loaded = load(config, number, target, build, on_behalf_of=on_behalf_of)
    ledger = loaded.snapshot.ledger
    ids = [ledger.resolve(reference, list(loaded.snapshot.items)) for reference in references]
    if len(set(ids)) != len(ids):
        raise QewError("an item is named twice", 2)
    entries = [(item_id, _entry_text(loaded.snapshot, item_id)) for item_id in ids]
    with forge_errors(loaded.record.target):
        first = loaded.forge.read_pull_request(number)
    refuse_finished(number, first, "have its checklist edited")
    edited = pr_checklist.add(first.body, entries)
    _write_body(loaded, number, first, edited)
    for item_id in loaded.record.items or (loaded.record.host,):
        ledger.append_event(item_id, "commented", text=f"added progress entries to pull request #{number}: {' '.join(ids)}", author=actor())
    _cover(ledger, loaded.record, ids)
    return f"pr #{number} added: {' '.join(ids)}"


def _cover(ledger: Ledger, record: pr_record.PrRecord, wanted: list[str]) -> list[str]:
    """Append the items not yet covered to the pull request record; returns the ones added."""
    added = [i for i in dict.fromkeys(wanted) if i not in record.items]
    if added:
        pr_record.update_mirror(ledger, record, items=[*record.items, *added])
    return added


def sync_items(config: Config, number: int, target: str | None, build: Build | None = None) -> str:
    """Re-derive the covered items: the recorded ones, the root's open descendants, and every item with a Progress box.

    Only adds: a covered item never leaves the record here (that is `--drop`). Items whose box was dropped are not re-added."""
    loaded = load(config, number, target, build)
    snapshot = loaded.snapshot
    with forge_errors(loaded.record.target):
        body = loaded.forge.read_pull_request(number)
    refuse_finished(number, body, "have its covered items changed")
    dropped = {str(d).split(":", 1)[0] for d in next(m for m in snapshot.ledger.load_mirrors(loaded.record.host) if m["_target"] == loaded.record.mirror_file).get("dropped", [])}
    boxed = [b.address for b in pr_checklist.boxes(body.body) if b.address in snapshot.items]
    wanted = [i for i in [*pr_record.covered_by(snapshot, loaded.record.host), *boxed] if i not in dropped]
    added = _cover(snapshot.ledger, loaded.record, wanted)
    if added:
        for item_id in loaded.record.items:
            snapshot.ledger.append_event(item_id, "commented", text=f"pull request #{number} now covers: {' '.join(added)}", author=actor())
    return f"pr #{number} covers {' '.join([*loaded.record.items, *added])}" + (f" (added {' '.join(added)})" if added else " (unchanged)")


def _drop_item(config: Config, number: int, name: str, reason: str, force: bool, target: str | None, build: Build | None, on_behalf_of: str | None) -> str:
    """Remove one checklist box from the body, keep every other byte, and record the drop on the item and the pull request record."""
    loaded = load(config, number, target, build, on_behalf_of=on_behalf_of)
    with forge_errors(loaded.record.target):
        first = loaded.forge.read_pull_request(number)
    refuse_finished(number, first, "have its checklist edited")
    (box,) = pr_checklist.resolve(pr_checklist.boxes(first.body), [name])
    if box.ticked and not force:
        raise QewError(f"checklist item `{name}` is already ticked: it is finished work; pass --force to drop it anyway", 2)
    edited = pr_checklist.drop(first.body, box)
    _write_body(loaded, number, first, edited)
    ledger = loaded.snapshot.ledger
    text = reason.strip()
    for item_id in loaded.record.items or (loaded.record.host,):
        ledger.append_event(item_id, "commented", text=f"dropped checklist item {box.address} ({box.text}) from pull request #{number}: {text}", author=actor())
    current = next(m for m in ledger.load_mirrors(loaded.record.host) if m["_target"] == loaded.record.mirror_file)
    pr_record.update_mirror(ledger, loaded.record, dropped=[*current.get("dropped", []), f"{box.address}: {text}"])
    return f"pr #{number} dropped: {box.address}"


# -- ready -------------------------------------------------------------------------

@dataclass(frozen=True)
class Readied:
    number: int
    changed: bool

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "ready": True, "changed": self.changed, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} ready" if self.changed else f"pr #{self.number} already ready (unchanged)"


def mark_ready(
    config: Config, number: int, target: str | None, build: Build | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Readied:
    loaded = load(config, number, target, build)
    refuse_held(loaded, "be marked ready")
    forge = loaded.forge
    with forge_errors(loaded.record.target):
        before = forge.read_pull_request(number)
        refuse_finished(number, before, "be marked ready")
        outcome = apply_and_verify(
            before,
            lambda: forge.mark_pull_request_ready(number),
            lambda: forge.read_pull_request(number),
            lambda pull: not pull.draft,
            sleep=sleep,
        )
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} is still a draft after the forge accepted the change; nothing is known to have changed", 4)
    pr_record.update_mirror(loaded.snapshot.ledger, loaded.record, draft=False)
    return Readied(number, outcome == Outcome.VERIFIED)


@dataclass(frozen=True)
class Drafted:
    number: int
    changed: bool

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "draft": True, "changed": self.changed, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} draft" if self.changed else f"pr #{self.number} already a draft (unchanged)"


def mark_draft(
    config: Config, number: int, target: str | None, build: Build | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Drafted:
    """Move a recorded pull request back to draft (same ownership as ready), read back; a pull request that is not an open
    ready one is refused or unchanged. The inverse of `mark_ready`, so no covered item's state is touched."""
    loaded = load(config, number, target, build)
    forge = loaded.forge
    with forge_errors(loaded.record.target):
        before = forge.read_pull_request(number)
        refuse_finished(number, before, "be moved back to draft")
        outcome = apply_and_verify(
            before,
            lambda: forge.mark_pull_request_draft(number),
            lambda: forge.read_pull_request(number),
            lambda pull: pull.draft,
            sleep=sleep,
        )
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} is still ready after the forge accepted the change; nothing is known to have changed", 4)
    pr_record.update_mirror(loaded.snapshot.ledger, loaded.record, draft=True)
    return Drafted(number, outcome == Outcome.VERIFIED)


@dataclass(frozen=True)
class Rebodied:
    number: int
    changed: bool

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "changed": self.changed, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} body replaced" if self.changed else f"pr #{self.number} body (unchanged)"


def replace_body(config: Config, number: int, body_file: str, target: str | None, build: Build | None = None, on_behalf_of: str | None = None) -> Rebodied:
    """Replace a recorded, open pull request's body with the file's text (same ownership as ready), keeping the Progress boxes."""
    proposed = _body_file(body_file)
    if not proposed.strip():
        raise QewError(f"--body-file {body_file} is empty", 2)
    pr_body.refuse_closing_references(proposed, "the new body")
    loaded = load(config, number, target, build, on_behalf_of=on_behalf_of)
    with forge_errors(loaded.record.target):
        first = loaded.forge.read_pull_request(number)
    refuse_finished(number, first, "have its body replaced")
    edited = pr_checklist.replace(first.body, proposed)
    if edited == first.body:
        return Rebodied(number, False)
    _write_body(loaded, number, first, edited)
    return Rebodied(number, True)


# -- label -------------------------------------------------------------------------

@dataclass(frozen=True)
class Labelled:
    number: int
    changed: bool
    labels: tuple[str, ...]

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "changed": self.changed, "labels": list(self.labels), "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} labels: {', '.join(self.labels) or 'none'}" + ("" if self.changed else " (unchanged)")


def _label_name(name: str) -> str:
    if not name.strip() or name != name.strip() or len(name) > 50 or "," in name or not name.isprintable():
        raise QewError(f"label {name!r} is not a label name: 1 to 50 printable characters, no comma, no edge whitespace", 2)
    return name


def label_pull_request(
    config: Config, number: int, add: list[str], remove: list[str], target: str | None, build: Build | None = None,
    sleep: Callable[[float], None] | None = None, on_behalf_of: str | None = None,
) -> Labelled:
    """Add and remove labels on a recorded pull request (same ownership as ready, judged for `on_behalf_of` when given), keeping
    every other label, and succeed only once the forge shows the wanted set."""
    adding, removing = [_label_name(n) for n in dict.fromkeys(add)], [_label_name(n) for n in dict.fromkeys(remove)]
    if not adding and not removing:
        raise QewError("name labels to --add or --remove", 2)
    clash = sorted(set(adding) & set(removing))
    if clash:
        raise QewError(f"{', '.join(clash)} is both added and removed", 2)
    loaded = load(config, number, target, build, on_behalf_of=on_behalf_of)
    forge = loaded.forge
    with forge_errors(loaded.record.target):
        before = forge.read_pull_request(number)
        wanted = tuple(dict.fromkeys([*(l for l in before.labels if l not in removing), *adding]))
        if set(wanted) == set(before.labels):
            return Labelled(number, False, before.labels)
        outcome = apply_and_verify(
            before,
            lambda: forge.set_pull_request_labels(number, wanted),
            lambda: forge.read_pull_request(number),
            lambda pull: set(pull.labels) == set(wanted),
            sleep=sleep,
        )
        after = forge.read_pull_request(number)
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} does not show {', '.join(adding + removing)} after the forge accepted the change; nothing is known to have changed", 4)
    return Labelled(number, True, after.labels)


# -- sync --------------------------------------------------------------------------

@dataclass(frozen=True)
class Retitled:
    number: int
    changed: bool

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "retitled": self.changed, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} title updated" if self.changed else f"pr #{self.number} title unchanged"


def sync_title(
    config: Config, number: int, target: str | None, build: Build | None = None, sleep: Callable[[float], None] | None = None,
) -> Retitled:
    """Set an open pull request's title to its item's current title, read it back and record the change on the item.

    Only the title: the body is composed once at open from --what/--why/--testing and carries the checklist's ticks, so it is
    never regenerated."""
    loaded = load(config, number, target, build)
    wanted = loaded.snapshot.items[loaded.record.host].title
    forge = loaded.forge
    with forge_errors(loaded.record.target):
        before = forge.read_pull_request(number)
        refuse_finished(number, before, "be retitled")
        if before.title == wanted:
            return Retitled(number, False)
        outcome = apply_and_verify(
            before,
            lambda: forge.update_pull_request_title(number, wanted),
            lambda: forge.read_pull_request(number),
            lambda pull: pull.title == wanted,
            sleep=sleep,
        )
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} still has its old title after the forge accepted the change; nothing is known to have changed", 4)
    loaded.snapshot.ledger.append_event(
        loaded.record.host, "commented", text=f"pull request #{number} retitled: {before.title!r} -> {wanted!r}", author=actor())
    return Retitled(number, True)


# -- close -------------------------------------------------------------------------

@dataclass(frozen=True)
class Closed:
    number: int
    changed: bool

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "closed": True, "changed": self.changed, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} closed" if self.changed else f"pr #{self.number} already closed (unchanged)"


def close_pull_request(
    config: Config, number: int, target: str | None, build: Build | None = None,
    sleep: Callable[[float], None] | None = None, item: str | None = None, reason: str | None = None,
) -> Closed:
    """Close a recorded, unmerged pull request, read it back, then record `closed` in its mirror.

    `item` names the item that carries the remainder of a pull request qew never recorded (a legacy draft): it is
    adopted onto that item and closed, with `reason` kept in the ledger. A merged one is refused either way."""
    if item is not None:
        return _close_legacy(config, number, target, build, sleep, item, reason)
    loaded = load(config, number, target, build)
    forge = loaded.forge
    with forge_errors(loaded.record.target):
        before = forge.read_pull_request(number)
        if before.state == "merged":
            raise QewError(f"pr #{number} is merged; a merged pull request cannot be closed", 2)
        outcome = apply_and_verify(
            before,
            lambda: forge.close_pull_request(number),
            lambda: forge.read_pull_request(number),
            lambda pull: pull.state == "closed",
            sleep=sleep,
        )
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} is still open after the forge accepted the close; nothing is known to have changed", 4)
    pr_record.update_mirror(loaded.snapshot.ledger, loaded.record, state="closed")
    return Closed(number, outcome == Outcome.VERIFIED)


def _close_legacy(config: Config, number: int, target: str | None, build: Build | None, sleep: Callable[[float], None] | None,
                  item: str, reason: str | None) -> Closed:
    pr_record.positive_number(number)
    if not (reason or "").strip():
        raise QewError("closing an unrecorded pull request needs --reason TEXT: say why it is closed", 2)
    ledger = ledger_for(config)
    try:
        recorded = pr_record.find(ledger, number, target)
    except QewError:
        recorded = None
    snapshot = ledger.snapshot()
    item_id = ledger.resolve(item, list(snapshot.items))
    if recorded is not None:
        if item_id in recorded.items:  # a rerun of the adoption: closed already is the answer, else the ordinary close
            with forge_errors(recorded.target):
                if _build(build, mirror_for(config, ledger, recorded.target)).read_pull_request(number).state == "closed":
                    return Closed(number, True)
            return close_pull_request(config, number, target, build, sleep)
        raise QewError(f"pull request #{number} is already recorded for {' '.join(recorded.items)}; close it without --item", 2)
    mirrors = [m for m in mirrors_with_boards(ledger, config) if not target or target in ledger_config.mirror_slug(m)]
    if len(mirrors) != 1:
        raise QewError(f"{len(mirrors)} mirror targets match; pass --target", 2)
    mirror = mirrors[0]
    slug = ledger_config.mirror_slug(mirror)
    forge = _build(build, mirror)
    with forge_errors(slug):
        before = forge.read_pull_request(number)
        if before.state == "merged":
            raise QewError(f"pr #{number} is merged; a merged pull request cannot be closed", 2)
        outcome = Outcome.VERIFIED
        if before.state == "open":
            outcome = apply_and_verify(
                before,
                lambda: forge.close_pull_request(number),
                lambda: forge.read_pull_request(number),
                lambda pull: pull.state == "closed",
                sleep=sleep,
            )
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} is still open after the forge accepted the close; nothing is known to have changed", 4)
    text = (reason or "").strip()
    ledger.write_mirror(item_id, f"{slug}-pr-{number}", {
        "forge": mirror.forge, "url": mirror.repo, "kind": "pr", "draft": before.draft, "state": "closed", "number": number,
        "pr_url": before.url, "head": before.head_branch, "opener": "", "items": [item_id], "synced_at": now(),
    })
    ledger.append_event(item_id, "commented", text=f"closed legacy pull request #{number}: {text}", author=actor())
    return Closed(number, outcome == Outcome.VERIFIED)


# -- merge -------------------------------------------------------------------------

@dataclass(frozen=True)
class MergeOptions:
    auto: bool = False
    admin: bool = False
    other_author: bool = False
    reason: str | None = None
    check: bool = False


@dataclass(frozen=True)
class Merged:
    number: int
    head_sha: str
    merge_sha: str
    merged: bool  # False when auto-merge was only enabled
    items: tuple[str, ...]
    lines: tuple[str, ...]

    def render(self, as_json: bool) -> str:
        if as_json:
            payload = {"merged": self.merged, "number": self.number, "head_sha": self.head_sha, "items": list(self.items), "schema_version": SCHEMA_VERSION}
            if not self.merged:
                payload["auto_merge"] = True
            return json.dumps(payload, sort_keys=True)
        return "\n".join(self.lines)


def _merge_options(options: MergeOptions) -> str:
    reason = (options.reason or "").strip()
    if options.auto and options.admin:
        raise QewError("--auto and --admin contradict each other: auto waits for checks, admin skips them", 2)
    if (options.admin or options.other_author) and not reason:
        raise QewError("--admin and --other-author each need --reason TEXT", 2)
    if reason and not (options.admin or options.other_author):
        raise QewError("--reason only goes with --admin or --other-author", 2)
    return reason


def _enable_auto(loaded: Loaded, pull: RemotePullRequest) -> RemotePullRequest:
    forge, target = loaded.forge, loaded.record.target
    try:
        required = forge.requires_status_checks(pull.base_branch)
    except Unsupported:
        raise QewError(f"{target} cannot report whether {pull.base_branch} requires status checks; --auto is refused", 4) from None
    except ForgeError as error:
        raise QewError(f"{target}: cannot read the rules of {pull.base_branch}: {error.detail}", 4) from None
    if not required:
        raise QewError(
            f"pr #{pull.number} would merge immediately: {pull.base_branch} requires no status checks, so --auto would skip CI", 3
        )
    try:
        after = forge.enable_auto_merge(pull.number)
    except Unsupported:
        raise QewError(f"{target} does not support auto-merge; nothing was changed", 3) from None
    if not after.auto_merge:
        raise QewError(f"pr #{pull.number} does not show auto-merge enabled after the forge accepted it", 3)
    return after


def _hand_off(loaded: Loaded, number: int) -> list[str]:
    """What is left per covered item once the merge landed; the gate already proved evidence and children."""
    lines = []
    for item_id in pr_gate.open_work(loaded.states, loaded.record):
        if loaded.states[item_id] != State.CLAIMED:
            lines.append(f"{item_id}: {loaded.states[item_id].value}, unclaimed; nothing to complete yet")
        elif loaded.snapshot.items[item_id].kind == Kind.EPIC:
            lines.append(f"{item_id}: qew complete {item_id} (after its children)")
        else:
            lines.append(f"{item_id}: evidence recorded; qew complete {item_id}")
    lines.append(f"or land everything at once: qew land {number}")
    return lines


def _branch_line(cleanup: branch_cleanup.HeadFate, branch: str) -> list[str]:
    """The merge output about the head branch; a failure to delete it is a warning on stderr, never an error."""
    if cleanup.fate == branch_cleanup.Fate.DELETED:
        return [f"branch {branch} deleted"]
    if cleanup.fate == branch_cleanup.Fate.KEPT:
        return [f"branch {branch} kept: {cleanup.note}"]
    print(f"qew: branch {branch} not deleted: {untrusted_text.show(cleanup.note).text}", file=sys.stderr)
    return []


def _merge_gates(config: Config, loaded: Loaded, pull: RemotePullRequest, options: MergeOptions) -> list[Callable[[], None]]:
    """Every refusal `pr merge` applies before it touches anything, in the order it applies them; each raises `QewError`."""
    number = loaded.record.number

    def draft() -> None:
        if pull.draft:
            raise QewError(f"pr #{number} is a draft; run `qew pr ready {number}`", 2)

    def mergeable() -> None:
        if pull.mergeable is None:
            raise QewError(f"pr #{number} merge state unknown, retry in a moment", 2)
        if not pull.mergeable:
            raise QewError(f"pr #{number} has conflicts with {pull.base_branch}", 2)

    def boxes() -> None:
        unticked = [b for b in pr_checklist.boxes(pull.body) if not b.ticked]
        if unticked:
            raise QewError(f"pr #{number} has unticked checklist items: {'; '.join(b.address + ' ' + b.text for b in unticked)}", 2)

    def checks() -> None:
        if pull.checks not in ("passing", "none", "failing", "pending"):
            raise QewError(f"pr #{number} checks are {pull.checks!r}, which is not a known state; refusing to guess", 4)
        if pull.checks == "failing" and not options.admin:
            raise QewError(f"pr #{number} checks are failing", 3)
        if pull.checks == "pending" and not options.admin and not options.auto:
            raise QewError(f"pr #{number} checks are pending (wait, or pass --auto where the base branch requires checks)", 3)

    return [
        lambda: refuse_held(loaded, "merge"),
        lambda: refuse_finished(number, pull, "merge"),
        draft, mergeable, boxes,
        lambda: pr_gate.refuse_open_children(loaded.snapshot, loaded.record, loaded.states),
        lambda: pr_gate.refuse_missing_evidence(config, loaded.snapshot, loaded.record, loaded.states, pull.head_sha),
        checks,
    ]


def merge_check(
    config: Config, number: int, target: str | None, options: MergeOptions = MergeOptions(), build: Build | None = None,
) -> list[QewError]:
    """Every refusal `pr merge` would raise for these options, without merging or writing anything; empty when it would go ahead."""
    pr_record.positive_number(number)
    reason = _merge_options(options)
    try:
        loaded = load(config, number, target, build, reason if options.other_author else "", merging=True)
        with forge_errors(loaded.record.target):
            pull = loaded.forge.read_pull_request(number)
    except QewError as error:
        return [error]
    found: list[QewError] = []
    for gate in _merge_gates(config, loaded, pull, options):
        try:
            gate()
        except QewError as error:
            found.append(error)
    return found


def merge_pull_request(
    config: Config, number: int, target: str | None, options: MergeOptions = MergeOptions(),
    build: Build | None = None, sleep: Callable[[float], None] | None = None,
) -> Merged:
    pr_record.positive_number(number)
    reason = _merge_options(options)
    loaded = load(config, number, target, build, reason if options.other_author else "", merging=True)
    forge, slug = loaded.forge, loaded.record.target
    with forge_errors(slug):
        pull = forge.read_pull_request(number)
    for gate in _merge_gates(config, loaded, pull, options):
        gate()
    waiting = pull.checks == "pending" and not options.admin
    covered = tuple(loaded.record.items)
    lead = [loaded.note] if loaded.note else []
    if waiting:
        with forge_errors(slug):
            enabled = _enable_auto(loaded, pull)
        lines = (*lead, f"pr #{number} auto-merge enabled at {pull.head_sha[:12]}; it merges when the checks pass", *_hand_off(loaded, number))
        return Merged(number, pull.head_sha, enabled.merge_sha, False, covered, lines)
    with forge_errors(slug):
        try:
            outcome = apply_and_verify(
                pull,
                lambda: forge.merge_pull_request(number, pull.head_sha, bypass_checks=options.admin),
                lambda: forge.read_pull_request(number),
                lambda current: current.state == "merged",
                delays=(MERGE_RECHECK_SECONDS,),
                sleep=sleep,
            )
        except Unsupported:
            raise QewError(f"{slug} cannot merge past branch protection; nothing was merged", 3) from None
    if outcome == Outcome.NOT_APPLIED:
        raise QewError(f"pr #{number} merge not visible: the forge accepted the call but the pull request is still open", 3)
    ledger = loaded.snapshot.ledger
    pr_record.update_mirror(ledger, loaded.record, draft=False, state="merged")
    if options.admin:
        for item_id in pr_gate.open_work(loaded.states, loaded.record):
            ledger.append_event(item_id, "field", key="admin_merge", old="", new=reason)
        lead.append(f"admin merge: {reason}")
    with forge_errors(slug):
        merged = forge.read_pull_request(number)
    where = f" (merge commit {merged.merge_sha[:12]})" if merged.merge_sha else ""
    cleanup = branch_cleanup.delete_merged_head(forge, pull.head_branch, {pull.base_branch, config.target})
    branch = untrusted_text.show(cleanup.name).text
    lines = (*lead, f"pr #{number} merged at {pull.head_sha[:12]}{where}", *_branch_line(cleanup, branch), *_hand_off(loaded, number))
    return Merged(number, pull.head_sha, merged.merge_sha, True, covered, lines)
