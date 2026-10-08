"""Guarded claim and completion behavior."""

from dataclasses import dataclass
from enum import Enum
import re
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Union

from qew.claims import (
    DEFAULT_KIND,
    Claim,
    claim_comment,
    current_claim,
    release_comment,
    decode_comments,
    validate_kind,
    validate_lane,
    validate_workspace,
)
from qew.epic_claims import reconcile_parent
from qew.errors import QewError
from qew.github import (
    claim_issue,
    close_issue,
    comment_issue,
    compare_commit_to_branch,
    count_held_claims,
    edit_issue,
    fetch_claim_comments,
    fetch_commit_pulls,
    fetch_issue,
    release_claim,
    reopen_issue,
)
from qew.model import normalize_labels


class ClosureKind(str, Enum):
    COMPLETED = ("completed", "COMPLETED")
    DUPLICATE = ("duplicate", "DUPLICATE")
    ABANDONED = ("not planned", "NOT_PLANNED")

    def __new__(cls, gh_reason: str, state_reason: str):
        value = str.__new__(cls, gh_reason)
        value._value_ = gh_reason
        value.state_reason = state_reason
        return value


@dataclass(frozen=True)
class LandedCommit:
    """Completion evidence: COMMIT must already be on the remote BRANCH."""

    commit: str
    branch: str


@dataclass(frozen=True)
class NoCommit:
    """Completion evidence: an explicit, recorded statement that no commit exists."""

    reason: str


CompletionEvidence = Union[LandedCommit, NoCommit]

_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_LANDED_STATUSES = frozenset({"ahead", "identical"})


def completion_evidence(
    commit: Optional[str], branch: Optional[str], no_commit: Optional[str]
) -> CompletionEvidence:
    """Validate the completion flags before any GitHub call."""
    if no_commit is not None:
        if commit is not None or branch is not None:
            raise QewError("--no-commit cannot be combined with --commit or --landed-on", 2)
        if not no_commit.strip():
            raise QewError("--no-commit requires a reason saying why there is no commit", 2)
        return NoCommit(no_commit.strip())
    if commit is None or branch is None:
        raise QewError(
            "complete requires --commit SHA and --landed-on BRANCH, "
            "or --no-commit REASON when the work has no commit",
            2,
        )
    if not _COMMIT.fullmatch(commit):
        raise QewError("--commit must be a 7 to 40 character hexadecimal commit id", 2)
    if (
        not _BRANCH.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith(("/", ".", ".lock"))
    ):
        raise QewError(f"--landed-on is not a valid branch name: {branch!r}", 2)
    return LandedCommit(commit.lower(), branch)


@dataclass(frozen=True)
class WorkState:
    number: int
    state: str
    state_reason: Optional[str]
    viewer: str
    assignees: FrozenSet[str]
    labels: FrozenSet[str]
    open_blockers: FrozenSet[int]
    parent: Optional[int] = None


def validate_claim_request(
    lane: Optional[str], workspace: Optional[str], kind: Optional[str] = None
) -> None:
    if workspace is not None and lane is None:
        raise QewError("--workspace requires --lane", 2)
    if kind is not None and lane is None:
        raise QewError("--kind requires --lane", 2)
    if lane is not None:
        validate_lane(lane)
    if workspace is not None:
        validate_workspace(workspace)
    if kind is not None:
        validate_kind(kind)


def start(
    repository: str,
    issue: int,
    priority: Optional[str] = None,
    lane: Optional[str] = None,
    workspace: Optional[str] = None,
    kind: Optional[str] = None,
    claim_cap: Optional[int] = None,
    take_over: Optional[str] = None,
) -> Optional[Claim]:
    """Claim work; `take_over` is the reason for deliberately taking another login's claim."""
    validate_claim_request(lane, workspace, kind)
    if take_over is not None:
        _require_reason(take_over)
    before = decode_work_state(fetch_issue(repository, issue))
    _require_number(before, issue)
    _require_open(before)
    _require_approved_work(before)
    if take_over is not None:
        _require_other_claim(before)
        # Never free work that then may not start: check before the first write.
        _require_startable(before)
        release(repository, issue, f"taken over by {before.viewer}: {take_over}", force=True)
        before = decode_work_state(fetch_issue(repository, issue))
        _require_number(before, issue)
    _require_lane_available(repository, before, lane)
    _require_available(before)
    _require_below_cap(repository, before.viewer, claim_cap)

    if priority is not None:
        before_labels = normalize_labels(list(before.labels))
        if before_labels.priorities:
            raise QewError("--priority is valid only when priority is missing", 2)
        if len(before_labels.work_types) != 1:
            raise QewError(f"#{issue} requires exactly one work type before priority triage", 2)
        edit_issue(repository, issue, ["--add-label", priority])
        before = decode_work_state(fetch_issue(repository, issue))
        normalized = normalize_labels(list(before.labels))
        if (
            before.number != issue
            or len(normalized.priorities) != 1
            or normalized.priority is None
            or normalized.priority.value != priority
        ):
            raise QewError(
                f"priority {priority} may have been applied to #{issue}; verification failed",
                3,
            )
        try:
            _require_open(before)
            _require_approved_work(before)
            _require_lane_available(repository, before, lane)
            _require_available(before)
        except QewError as error:
            raise QewError(
                f"priority {priority} was applied to #{issue}; claim stopped: {error}",
                error.exit_code,
            ) from error
    elif not normalize_labels(list(before.labels)).triaged:
        raise QewError(f"#{issue} is untriaged; pass --priority only when priority is missing", 2)

    try:
        claim_issue(repository, issue)
    except QewError as error:
        detail = f"priority {priority} was applied; " if priority is not None else ""
        raise QewError(f"{detail}claim write failed for #{issue}: {error}", error.exit_code) from error
    after = decode_work_state(fetch_issue(repository, issue))
    if (
        after.number != issue
        or after.state != "OPEN"
        or after.viewer not in after.assignees
        or "in-progress" not in after.labels
    ):
        raise QewError(f"claim may have succeeded; verification failed for #{issue}", 3)
    competing = after.assignees - {after.viewer}
    if competing:
        owners = ", ".join(sorted(competing))
        raise QewError(f"claim conflict on #{issue} with {owners}", 3)
    reconcile_parent(repository, after.parent)
    if lane is None:
        return None
    try:
        comment_issue(repository, issue, claim_comment(lane, workspace, kind or DEFAULT_KIND))
    except QewError as error:
        raise QewError(
            f"#{issue} is claimed but its lane record failed: {error}; "
            "release the claim or retry the lane record",
            error.exit_code,
        ) from error
    claim = issue_claim(repository, issue, after.assignees)
    if claim is not None and claim.lane != lane:
        raise QewError(f"claim conflict on #{issue} with lane {claim.lane}", 3)
    if (
        claim is None
        or claim.workspace != workspace
        or claim.kind != (kind or DEFAULT_KIND)
        or claim.by != after.viewer
    ):
        raise QewError(f"lane record may have been written; verification failed for #{issue}", 3)
    return claim


# Distinct from usage errors (2) so an agent loop can tell "wait" from "wrong".
AT_CAP_EXIT = 6


def _require_below_cap(repository: str, viewer: str, claim_cap: Optional[int]) -> None:
    # Counted per login: the config is shared by every person in the repository.
    # Approximate by design: the count is read before the claim is written, so
    # starts racing from one login can each pass and exceed the cap briefly.
    if claim_cap is None:
        return
    held = count_held_claims(repository, viewer)
    if held >= claim_cap:
        raise QewError(
            f"{viewer} already holds {held} open claims; max_active_claims {claim_cap} "
            "in .qew/config.json allows no more",
            AT_CAP_EXIT,
        )


def issue_claim(repository: str, issue: int, assignees) -> Optional[Claim]:
    """The current lane claim of a claimed issue, from comments by its assignees."""
    comments = decode_comments(fetch_claim_comments(repository, issue), issue)
    return current_claim(
        comments,
        sorted(assignees),
        lambda message: print(f"warning: {message} on #{issue}", file=sys.stderr),
    )


@dataclass(frozen=True)
class Release:
    lane: Optional[str]
    by: str


def release(
    repository: str,
    issue: int,
    reason: str,
    *,
    force: bool,
    lane: Optional[str] = None,
) -> Release:
    """Free a claim without closing the work, and record who did it and why.

    Lanes share one login, so the caller names its lane: releasing a claim held
    by a different lane (or a lane claim without naming a lane) needs --force,
    which is recorded. This guards against accidents between lanes acting in
    good faith; it is not an access control.
    """
    _require_reason(reason)
    if lane is not None:
        validate_lane(lane)
    before = decode_work_state(fetch_issue(repository, issue))
    _require_number(before, issue)
    _require_open(before)
    if not before.assignees and "in-progress" not in before.labels:
        raise QewError(f"#{issue} has no claim to release", 2)
    others = before.assignees - {before.viewer}
    if others and not force:
        owners = ", ".join(sorted(others))
        raise QewError(
            f"#{issue} is claimed by {owners}; pass --force to release another login's claim",
            3,
        )
    holder = issue_claim(repository, issue, before.assignees) if before.assignees else None
    held_lane = None if holder is None else holder.lane
    forced_by_lane = None
    if held_lane is not None and held_lane != lane:
        if not force:
            raise QewError(
                f"#{issue} is claimed by lane {held_lane}; pass --lane {held_lane}, "
                "or --force to release another lane's claim",
                3,
            )
        forced_by_lane = lane or "(none)"
    release_claim(repository, issue, sorted(before.assignees))
    try:
        comment_issue(
            repository,
            issue,
            release_comment(held_lane, before.viewer, reason, forced_by_lane),
        )
    except QewError as error:
        raise QewError(
            f"#{issue} was released but its release record failed: {error}",
            error.exit_code,
        ) from error
    after = decode_work_state(fetch_issue(repository, issue))
    if after.number != issue or after.assignees or "in-progress" in after.labels:
        raise QewError(f"release verification failed for #{issue}", 3)
    reconcile_parent(repository, before.parent)
    return Release(lane=held_lane, by=before.viewer)


def _require_lane_available(repository: str, work: WorkState, lane: Optional[str]) -> None:
    """A start on work the caller already holds names the holding lane.

    Without --lane this matters too: agent loops read exit 5 ("already claimed
    by you") as "I hold it", so a sibling lane must see exit 3 instead.
    """
    if work.viewer not in work.assignees:
        return
    if work.open_blockers or normalize_labels(list(work.labels)).needs_approval:
        return
    holder = issue_claim(repository, work.number, sorted(work.assignees))
    if lane is None:
        if holder is not None:
            raise QewError(f"#{work.number} is already claimed by lane {holder.lane}", 3)
        return
    if holder is None:
        raise QewError(f"#{work.number} is already claimed by you without a lane", 3)
    if holder.lane != lane:
        raise QewError(f"#{work.number} is already claimed by lane {holder.lane}", 3)
    raise QewError(f"#{work.number} is already claimed by lane {lane}", 5)


def _require_approved_work(work: WorkState) -> None:
    normalized = normalize_labels(list(work.labels))
    _require_not_draft(work)
    if not normalized.approved or normalized.epic:
        raise QewError(f"#{work.number} is not approved typed work", 2)


def _require_not_draft(work: WorkState) -> None:
    if normalize_labels(list(work.labels)).draft:
        raise QewError(
            f"#{work.number} is a draft; approve it first with qew draft approve {work.number}", 2
        )


def _require_startable(work: WorkState) -> None:
    if work.open_blockers:
        blockers = ", ".join(f"#{number}" for number in sorted(work.open_blockers))
        raise QewError(f"#{work.number} is blocked by {blockers}", 2)
    if normalize_labels(list(work.labels)).needs_approval:
        raise QewError(f"#{work.number} needs human approval before it may start", 2)


def _require_other_claim(work: WorkState) -> None:
    if not work.assignees - {work.viewer}:
        raise QewError(f"#{work.number} has nothing to take over: no other login holds it", 2)


def _take_over_hint(work: WorkState) -> str:
    others = work.assignees - {work.viewer}
    if not others:
        return ""
    return f" (held by {', '.join(sorted(others))}; pass --take-over to take it deliberately)"


def _taken_over_record(work: WorkState) -> str:
    owners = ", ".join(sorted(work.assignees - {work.viewer}))
    return f"\n\nTaken over from {owners} by {work.viewer}"


def _require_available(work: WorkState) -> None:
    _require_startable(work)
    if work.assignees:
        if work.viewer in work.assignees:
            raise QewError(f"#{work.number} is already claimed by you", 5)
        owners = ", ".join(sorted(work.assignees))
        raise QewError(f"#{work.number} is already claimed by {owners}", 3)


def complete(
    repository: str,
    issue: int,
    reason: str,
    evidence: CompletionEvidence,
    take_over: bool = False,
) -> None:
    _require_reason(reason)
    before = decode_work_state(fetch_issue(repository, issue))
    _require_number(before, issue)
    if take_over:
        _require_other_claim(before)
    if before.state == "CLOSED":
        _complete_auto_closed(repository, before, reason, evidence, take_over)
        return
    _require_open(before)
    _require_not_draft(before)
    if not take_over:
        if before.viewer not in before.assignees:
            raise QewError(f"#{issue} is not claimed by {before.viewer}{_take_over_hint(before)}", 3)
        if "in-progress" not in before.labels:
            raise QewError(f"#{issue} has no in-progress claim", 3)
        competing = before.assignees - {before.viewer}
        if competing:
            owners = ", ".join(sorted(competing))
            raise QewError(f"claim conflict on #{issue} with {owners}{_take_over_hint(before)}", 3)
    comment = _completion_comment(repository, reason, evidence)
    if take_over:
        comment += _taken_over_record(before)
    close_issue(
        repository,
        issue,
        reason="completed",
        comment=comment,
        assignees=sorted(before.assignees),
    )
    _verify_closed(fetch_issue(repository, issue), issue, "COMPLETED")
    reconcile_parent(repository, before.parent)


def _completion_comment(repository: str, reason: str, evidence: CompletionEvidence) -> str:
    """Prove the evidence (reads only) and render the comment that records it."""
    if isinstance(evidence, NoCommit):
        return f"{reason}\n\nNo commit: {evidence.reason}"
    comparison = compare_commit_to_branch(repository, evidence.commit, evidence.branch)
    status = comparison.get("status")
    base = comparison.get("base_commit")
    full = base.get("sha") if isinstance(base, dict) else None
    if not isinstance(status, str) or not isinstance(full, str):
        raise QewError(
            f"indeterminate: invalid comparison of {evidence.commit} with {evidence.branch}", 4
        )
    if not _FULL_COMMIT.fullmatch(full) or not full.startswith(evidence.commit):
        raise QewError(
            f"indeterminate: GitHub compared {full} when asked for {evidence.commit}", 4
        )
    if status not in _LANDED_STATUSES:
        raise QewError(f"commit {full} is not on {evidence.branch} ({status})", 2)
    merged = []
    for pull in fetch_commit_pulls(repository, full):
        number = pull.get("number")
        merged_at = pull.get("merged_at")
        if not isinstance(number, int) or isinstance(number, bool) or not (
            merged_at is None or isinstance(merged_at, str)
        ):
            raise QewError(f"indeterminate: invalid pull requests for commit {full}", 4)
        if merged_at:
            merged.append(number)
    record = f"Landed: commit {full} on {evidence.branch}"
    if merged:
        record += "\nPR: " + ", ".join(f"#{number}" for number in sorted(merged))
    return f"{reason}\n\n{record}"


def _complete_auto_closed(
    repository: str,
    before: WorkState,
    reason: str,
    evidence: CompletionEvidence,
    take_over: bool = False,
) -> None:
    if before.state_reason != "COMPLETED":
        raise QewError(
            f"#{before.number} is closed as {before.state_reason or 'unknown'}, not completed",
            2,
        )
    stale_label = "in-progress" in before.labels
    if not before.assignees and not stale_label:
        raise QewError(f"#{before.number} is closed with no claim to complete", 3)
    if not take_over:
        if before.assignees and before.viewer not in before.assignees:
            raise QewError(
                f"#{before.number} is not claimed by {before.viewer}{_take_over_hint(before)}", 3
            )
        competing = before.assignees - {before.viewer}
        if competing:
            owners = ", ".join(sorted(competing))
            raise QewError(
                f"claim conflict on #{before.number} with {owners}{_take_over_hint(before)}", 3
            )
    comment = _completion_comment(repository, reason, evidence)
    if take_over:
        comment += _taken_over_record(before)
    release_claim(repository, before.number, sorted(before.assignees))
    comment_issue(repository, before.number, comment)
    _verify_closed(fetch_issue(repository, before.number), before.number, "COMPLETED")
    reconcile_parent(repository, before.parent)


def close(
    repository: str,
    issue: int,
    reason: str,
    *,
    completed: bool = False,
    abandoned: bool = False,
    duplicate_of: Optional[int] = None,
    delivered_by: Optional[str] = None,
    landed_on: Optional[str] = None,
    superseded_by: Optional[int] = None,
) -> str:
    """Close work nobody claimed with the disposition the caller named.

    Abandoned and duplicate close as not planned and duplicate. Delivered and
    superseded work closes as completed: delivered carries the landing proof
    `complete` demands, superseded links the issue that replaced it.
    """
    if completed:
        # Completion goes through `complete`, which records the landed commit
        # (or the stated reason there is none) and releases the claim.
        raise QewError(
            "close --completed records no landing; for work a lane claimed use "
            f"qew complete {issue} --reason TEXT --commit SHA --landed-on BRANCH "
            "(or --no-commit REASON); for work somebody else delivered use "
            f"qew close {issue} --reason TEXT --delivered-by SHA --landed-on BRANCH",
            2,
        )
    chosen = [
        name
        for name, value in (
            ("--abandoned", abandoned),
            ("--duplicate-of", duplicate_of is not None),
            ("--delivered-by", delivered_by is not None),
            ("--superseded-by", superseded_by is not None),
        )
        if value
    ]
    if not chosen:
        raise QewError(
            f"close needs a disposition: --abandoned (not planned), --duplicate-of ISSUE, "
            f"--delivered-by SHA --landed-on BRANCH (the work landed), or --superseded-by ISSUE; "
            f"to finish work you claimed use qew complete {issue} --reason TEXT "
            f"--commit SHA --landed-on BRANCH",
            2,
        )
    if len(chosen) > 1:
        raise QewError(f"close takes one disposition, got {' and '.join(chosen)}", 2)
    if landed_on is not None and delivered_by is None:
        raise QewError("--landed-on belongs to --delivered-by", 2)
    _require_reason(reason)
    for name, number in (("duplicate", duplicate_of), ("superseding", superseded_by)):
        if number is not None and number <= 0:
            raise QewError(f"{name} issue number must be positive", 2)
    if duplicate_of == issue:
        raise QewError(f"#{issue} cannot be a duplicate of itself", 2)
    if superseded_by == issue:
        raise QewError(f"#{issue} cannot be superseded by itself", 2)
    evidence = None
    if delivered_by is not None:
        if landed_on is None:
            raise QewError("--delivered-by requires --landed-on BRANCH", 2)
        evidence = completion_evidence(delivered_by, landed_on, None)
    before = decode_work_state(fetch_issue(repository, issue))
    _require_number(before, issue)
    _require_open(before)
    comment = reason
    if duplicate_of is not None:
        kind = ClosureKind.DUPLICATE
        output = f"duplicate of #{duplicate_of}"
    elif abandoned:
        kind = ClosureKind.ABANDONED
        output = "abandoned"
    elif evidence is not None:
        kind = ClosureKind.COMPLETED
        comment = _completion_comment(repository, reason, evidence)
        output = f"completed, delivered by {evidence.commit[:7]} on {evidence.branch}"
    else:
        kind = ClosureKind.COMPLETED
        _require_number(decode_work_state(fetch_issue(repository, superseded_by)), superseded_by)
        comment = f"{reason}\n\nSuperseded by #{superseded_by}"
        output = f"completed, superseded by #{superseded_by}"
    close_issue(
        repository,
        issue,
        reason=kind.value,
        comment=comment,
        assignees=sorted(before.assignees),
        duplicate_of=duplicate_of,
    )
    _verify_closed(fetch_issue(repository, issue), issue, kind.state_reason)
    reconcile_parent(repository, before.parent)
    return output


def reopen(repository: str, issue: int, reason: str) -> bool:
    _require_reason(reason)
    before = decode_work_state(fetch_issue(repository, issue))
    _require_number(before, issue)
    if before.state == "OPEN":
        return False
    if before.state != "CLOSED":
        raise QewError(f"indeterminate: invalid state for #{issue}", 4)
    reopen_issue(
        repository,
        issue,
        reason=reason,
        assignees=sorted(before.assignees),
    )
    after = decode_work_state(fetch_issue(repository, issue))
    if (
        after.number != issue
        or after.state != "OPEN"
        or after.state_reason != "REOPENED"
        or after.assignees
        or "in-progress" in after.labels
    ):
        raise QewError(f"reopen verification failed for #{issue}", 3)
    reconcile_parent(repository, before.parent)
    return True


def decode_work_state(payload: Dict[str, Any]) -> WorkState:
    data = _mapping(payload, "data", "response")
    viewer = _text(_mapping(data, "viewer", "response data"), "login", "viewer")
    repository = _mapping(data, "repository", "response data")
    issue = _mapping(repository, "issue", "repository")
    number = _integer(issue, "number", "issue")
    assignees = _connection(issue, "assignees", number)
    labels = _connection(issue, "labels", number)
    blockers = _connection(issue, "blockedBy", number)
    return WorkState(
        number=number,
        state=_text(issue, "state", f"issue #{number}"),
        state_reason=_optional_text(issue, "stateReason", f"issue #{number}"),
        viewer=viewer,
        assignees=frozenset(_text(node, "login", "assignee") for node in assignees),
        labels=frozenset(_text(node, "name", "label") for node in labels),
        open_blockers=frozenset(
            _integer(node, "number", "blocker")
            for node in blockers
            if _text(node, "state", "blocker") != "CLOSED"
        ),
        parent=_parent_number(issue),
    )


def _parent_number(issue: Dict[str, Any]) -> Optional[int]:
    parent = issue.get("parent")
    number = parent.get("number") if isinstance(parent, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def _connection(issue: Dict[str, Any], key: str, number: int) -> List[Dict[str, Any]]:
    connection = _mapping(issue, key, f"issue #{number}")
    page_info = _mapping(connection, "pageInfo", f"{key} for #{number}")
    if page_info.get("hasNextPage") is not False:
        raise QewError(f"indeterminate: GitHub truncated {key} for #{number}", 4)
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid {key} for #{number}", 4)
    return nodes


def _require_open(work: WorkState) -> None:
    if work.state != "OPEN":
        raise QewError(f"#{work.number} is not open", 2)


def _require_number(work: WorkState, expected: int) -> None:
    if work.number != expected:
        raise QewError(f"indeterminate: GitHub returned #{work.number} for #{expected}", 4)


def _require_reason(reason: str) -> None:
    if not reason.strip():
        raise QewError("reason must not be empty", 2)


def _verify_closed(payload: Dict[str, Any], issue: int, state_reason: str) -> None:
    after = decode_work_state(payload)
    if (
        after.number != issue
        or after.state != "CLOSED"
        or after.state_reason != state_reason
        or after.assignees
        or "in-progress" in after.labels
    ):
        raise QewError(f"closure verification failed for #{issue}", 3)


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: missing {key} in {context}", 4)
    return child


def _integer(value: Dict[str, Any], key: str, context: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _text(value: Dict[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _optional_text(value: Dict[str, Any], key: str, context: str) -> Optional[str]:
    child = value.get(key)
    if child is not None and not isinstance(child, str):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child
