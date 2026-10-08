"""`qew pr`: open a pull request for an item, tick its checklist, mark it ready, merge only its checked head.

The pull request never carries a closing keyword: finishing work is `qew complete`
(or `qew land`), not a forge side effect. The ledger, not the forge, says who opened a
pull request and which items it covers.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import re
import sys
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from qew import branch_cleanup, forge_registry, ledger_config, pr_body, pr_checklist, pr_gate, pr_record, untrusted_text, vcs
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards
from qew.errors import QewError
from qew.forge import Forge, ForgeError, PullRequestSpec, RemotePullRequest, Unsupported
from qew.ledger_config import Config, Mirror
from qew.ledger_event import State
from qew.ledger_ids import valid_id
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot, actor, now
from qew.pr_record import PrRecord
from qew.pr_verify import Outcome, apply_and_verify

Build = Callable[[Mirror], Forge]
SCHEMA_VERSION = 1
LOGIN_VARIABLE = "QEW_FORGE_LOGIN"  # the forge login a pull request is assigned to, when it differs from the actor
MERGE_RECHECK_SECONDS = 1.0
_TARGET_HELP = "mirror target (slug or unique part)"
_REASON_HELP = "why: required with --admin and --other-author"


def register(commands) -> None:
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
    opened.add_argument("--json", action="store_true", help="machine-readable output")
    check = sub.add_parser("check", help="tick progress checklist items in a pull request body")
    check.add_argument("number", type=int, help="pull request number")
    check.add_argument("ids", nargs="*", help="checklist items to tick: marker id, position, or @position")
    check.add_argument("--all", action="store_true", help="tick every open item")
    check.add_argument("--list", action="store_true", help="show the items and whether they are ticked; write nothing")
    check.add_argument("--target", help=_TARGET_HELP)
    ready = sub.add_parser("ready", help="mark a draft pull request ready for review")
    ready.add_argument("number", type=int, help="pull request number")
    ready.add_argument("--target", help=_TARGET_HELP)
    ready.add_argument("--json", action="store_true", help="machine-readable output")
    merge = sub.add_parser("merge", help="merge a ready, conflict-free, green pull request at its checked head")
    merge.add_argument("number", type=int, help="pull request number")
    merge.add_argument("--target", help=_TARGET_HELP)
    merge.add_argument("--auto", action="store_true", help="merge once the checks pass, only where the base branch requires checks")
    merge.add_argument("--admin", action="store_true", help="merge past pending or failing checks only (needs --reason)")
    merge.add_argument("--other-author", action="store_true", help="merge a pull request someone else opened (needs --reason)")
    merge.add_argument("--reason", help=_REASON_HELP)
    merge.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command != "pr":
        return False
    config = ledger_config.load()
    command = arguments.pr_command
    if command == "open":
        options = OpenOptions(
            arguments.target, arguments.head, arguments.base, arguments.ready,
            arguments.what, arguments.why, arguments.testing, arguments.body_file,
        )
        opened = open_pull_request(config, arguments.item, options)
        print(opened.render(arguments.json))
        if opened.unassigned:
            print(
                f"qew: pr #{opened.number} was not assigned to {opened.unassigned} (the forge refused); "
                f"the opener is recorded in the ledger; set {LOGIN_VARIABLE} to your forge login",
                file=sys.stderr,
            )
    elif command == "check":
        print(check_pull_request(config, arguments.number, arguments.ids, arguments.all, arguments.list, arguments.target))
    elif command == "ready":
        print(mark_ready(config, arguments.number, arguments.target).render(arguments.json))
    else:
        options = MergeOptions(arguments.auto, arguments.admin, arguments.other_author, arguments.reason)
        print(merge_pull_request(config, arguments.number, arguments.target, options).render(arguments.json))
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


def _build(build: Optional[Build], mirror: Mirror) -> Forge:
    return (build or forge_registry.build)(mirror)


@dataclass(frozen=True)
class Loaded:
    """A recorded pull request with the ledger facts every command needs, and its forge."""

    snapshot: Snapshot
    record: PrRecord
    states: Dict[str, State]
    forge: Forge
    note: str  # printed before the result when an override was used


def load(
    config: Config, number: int, target: Optional[str], build: Optional[Build],
    reason: str = "", merging: bool = False, owned: bool = True,
) -> Loaded:
    """The recorded pull request, after the ownership rule (exit 3) and a readable-history check (exit 4).

    A read-only command passes `owned=False`: anyone may look.
    """
    pr_record.positive_number(number)
    ledger = ledger_for(config)
    record = pr_record.find(ledger, number, target)
    snapshot = ledger.snapshot()
    states = pr_record.known_states(snapshot, record)
    note = pr_record.require_owner(snapshot, record, states, actor(), reason, merging) if owned else ""
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
    target: Optional[str] = None
    head: Optional[str] = None
    base: Optional[str] = None
    ready: bool = False
    what: Optional[str] = None
    why: Optional[str] = None
    testing: Optional[str] = None
    body_file: Optional[str] = None


@dataclass(frozen=True)
class Opened:
    item: str
    number: int
    url: str
    draft: bool
    unassigned: str = ""  # the login the forge would not assign, "" when assigned

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps(
                {"item": self.item, "number": self.number, "url": self.url, "draft": self.draft, "schema_version": SCHEMA_VERSION},
                sort_keys=True,
            )
        return f"{self.item} pr #{self.number} {self.url}{' (draft)' if self.draft else ''}"


def _body_file(path: Optional[str]) -> str:
    if path is None:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read --body-file {path}: {error}", 2) from None


def _refuse_other_items_branch(snapshot: Snapshot, head: str, covered: List[str]) -> None:
    """A branch named for another item than the one being opened belongs to that other item."""
    tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", head) if len(t) >= 6}
    named = [i for i in snapshot.items if i.lower() in tokens and valid_id(i)]
    if named and not set(named) & set(covered):
        raise QewError(
            f"branch {head} names {' '.join(named)} but the pull request is for {covered[0]}; "
            "open it for that item or pass a different --head",
            2,
        )


def open_pull_request(config: Config, reference: str, options: OpenOptions = OpenOptions(), build: Optional[Build] = None) -> Opened:
    extra = _body_file(options.body_file)
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
    if len(issues) != 1:
        raise QewError(f"{item_id} has {len(issues)} matching issue mirrors; run `qew sync` first or pass --target", 2)
    issue = issues[0]
    head = vcs.synced_branch(options.head)
    _refuse_other_items_branch(snapshot, head, covered)
    what = options.what if options.what is not None else item.body
    body = pr_body.build_body(item_id, issue["number"], what, options.why, options.testing, extra)
    pr_body.refuse_closing_references(body, "the pull request body")
    mirror = mirror_for(config, ledger, issue["_target"])
    login = os.environ.get(LOGIN_VARIABLE) or actor()
    spec = PullRequestSpec(head, options.base or config.target, item.title, body, not options.ready, login)
    with forge_errors(issue["_target"]):
        remote = _build(build, mirror).open_pull_request(spec)
    ledger.write_mirror(
        item_id,
        f"{issue['_target']}-pr-{remote.number}",
        {
            "forge": mirror.forge,
            "url": mirror.repo,
            "kind": "pr",
            "draft": spec.draft,
            "state": "open",
            "number": remote.number,
            "pr_url": remote.url,
            "head": head,
            "opener": actor(),
            "items": covered,
            "synced_at": now(),
        },
    )
    return Opened(item_id, remote.number, remote.url, spec.draft, "" if remote.assignee == login else login)


# -- check -------------------------------------------------------------------------

def _describe(box: pr_checklist.Box) -> str:
    return f"[{'x' if box.ticked else ' '}] {box.address} {box.text}"


def check_pull_request(
    config: Config, number: int, names: List[str], every: bool, listing: bool, target: Optional[str],
    build: Optional[Build] = None,
) -> str:
    chosen = [bool(names), every, listing]
    if sum(chosen) != 1:
        raise QewError("name checklist items, or pass exactly one of --all and --list", 2)
    loaded = load(config, number, target, build, owned=not listing)
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
    with forge_errors(loaded.record.target):
        latest = loaded.forge.read_pull_request(number)
        if latest.body != first.body:
            raise QewError(f"pr #{number} body changed while it was being edited; nothing written, run again", 3)
        written = loaded.forge.update_pull_request_body(number, edited)
    if written.body != edited:
        raise QewError(f"pr #{number} body did not take the edit; the forge still shows the old checklist", 3)
    return f"pr #{number} checked: {' '.join(b.address for b in targets)}"


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
    config: Config, number: int, target: Optional[str], build: Optional[Build] = None,
    sleep: Optional[Callable[[float], None]] = None,
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


# -- merge -------------------------------------------------------------------------

@dataclass(frozen=True)
class MergeOptions:
    auto: bool = False
    admin: bool = False
    other_author: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True)
class Merged:
    number: int
    head_sha: str
    merge_sha: str
    merged: bool  # False when auto-merge was only enabled
    items: Tuple[str, ...]
    lines: Tuple[str, ...]

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


def _hand_off(loaded: Loaded, number: int) -> List[str]:
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


def _branch_line(cleanup: branch_cleanup.HeadFate, branch: str) -> List[str]:
    """The merge output about the head branch; a failure to delete it is a warning on stderr, never an error."""
    if cleanup.fate == branch_cleanup.Fate.DELETED:
        return [f"branch {branch} deleted"]
    if cleanup.fate == branch_cleanup.Fate.KEPT:
        return [f"branch {branch} kept: {cleanup.note}"]
    print(f"qew: branch {branch} not deleted: {untrusted_text.show(cleanup.note).text}", file=sys.stderr)
    return []


def merge_pull_request(
    config: Config, number: int, target: Optional[str], options: MergeOptions = MergeOptions(),
    build: Optional[Build] = None, sleep: Optional[Callable[[float], None]] = None,
) -> Merged:
    pr_record.positive_number(number)
    reason = _merge_options(options)
    loaded = load(config, number, target, build, reason if options.other_author else "", merging=True)
    forge, slug = loaded.forge, loaded.record.target
    refuse_held(loaded, "merge")
    with forge_errors(slug):
        pull = forge.read_pull_request(number)
    refuse_finished(number, pull, "merge")
    if pull.draft:
        raise QewError(f"pr #{number} is a draft; run `qew pr ready {number}`", 2)
    if pull.mergeable is None:
        raise QewError(f"pr #{number} merge state unknown, retry in a moment", 2)
    if not pull.mergeable:
        raise QewError(f"pr #{number} has conflicts with {pull.base_branch}", 2)
    unticked = [b for b in pr_checklist.boxes(pull.body) if not b.ticked]
    if unticked:
        raise QewError(f"pr #{number} has unticked checklist items: {'; '.join(b.address + ' ' + b.text for b in unticked)}", 2)
    pr_gate.refuse_open_children(loaded.snapshot, loaded.record, loaded.states)
    pr_gate.refuse_missing_evidence(config, loaded.snapshot, loaded.record, loaded.states, pull.head_sha)
    if pull.checks not in ("passing", "none", "failing", "pending"):
        raise QewError(f"pr #{number} checks are {pull.checks!r}, which is not a known state; refusing to guess", 4)
    if pull.checks == "failing" and not options.admin:
        raise QewError(f"pr #{number} checks are failing", 3)
    waiting = pull.checks == "pending" and not options.admin
    if waiting and not options.auto:
        raise QewError(f"pr #{number} checks are pending (wait, or pass --auto where the base branch requires checks)", 3)
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
