"""Move work between draft and approved: approve releases it, revert holds it back."""

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from qew.config import load_config
from qew.errors import QewError
from qew.github import (
    comment_issue,
    edit_issue,
    edit_pull_request_label,
    fetch_draft_node,
    fetch_mutation_target,
    fetch_pull_request_for_merge,
)
from qew.model import NormalizedLabels, normalize_labels
from qew.requester import require_quote

TREE_LIMIT = 1000

Selector = Callable[[NormalizedLabels], bool]
Planned = Tuple[List[int], List[int], Dict[int, Dict[str, Any]]]


@dataclass(frozen=True)
class Move:
    moved: List[int]
    skipped: List[int]
    # (issue, comment text) for moves whose explanatory comment could not be posted.
    unrecorded: Tuple[Tuple[int, str], ...] = ()
    # Pull request labels that could not be synced, as ready-to-run advice.
    notes: Tuple[str, ...] = ()


def approve(repository: str, issues: List[int], tree: bool, quote: str) -> Move:
    quote = require_quote(quote)
    roots = _roots(issues)
    targets, skipped, nodes = _plan(repository, roots, tree, lambda labels: labels.draft, "approve")
    move = _apply(
        repository,
        targets,
        skipped,
        "approved",
        lambda number: _write(
            repository,
            number,
            ["--add-label", "qew:approved", "--remove-label", "qew:draft"],
            f"Approved from draft.\n\n> {quote}",
            lambda after: after.approved and not after.draft,
        ),
    )
    pulls = _open_pulls(nodes, targets, lambda value: "qew:draft" in value["labels"])
    return _with_notes(move, _release_pull_requests(repository, pulls))


def revert(repository: str, issues: List[int], tree: bool, reason: str, quote: str) -> Move:
    """Return approved, unclaimed work (a plan with --tree its open descendants) to draft."""
    if not reason.strip():
        raise QewError("--reason must not be empty", 2)
    quote = require_quote(quote)
    roots = _roots(issues)
    targets, skipped, nodes = _plan(
        repository, roots, tree, lambda labels: labels.approved and not labels.draft, "revert"
    )
    _require_idle(targets, nodes)
    move = _apply(
        repository,
        targets,
        skipped,
        "reverted",
        lambda number: _write(
            repository,
            number,
            ["--add-label", "qew:draft", "--remove-label", "qew:approved"],
            f"Returned to draft: {reason.strip()}\n\n> {quote}",
            lambda after: after.draft and not after.approved,
        ),
    )
    pulls = _open_pulls(nodes, targets, lambda value: "qew:draft" not in value["labels"])
    return _with_notes(move, _hold_pull_requests(repository, pulls))


def _with_notes(move: Move, notes: List[str]) -> Move:
    return Move(move.moved, move.skipped, move.unrecorded, tuple(notes))


def _open_pulls(
    nodes: Dict[int, Dict[str, Any]],
    targets: List[int],
    wanted: Callable[[Dict[str, Any]], bool],
) -> List[int]:
    """Open pull requests that close any of the moved issues and pass `wanted`."""
    found: List[int] = []
    for number in targets:
        references = nodes[number].get("closedByPullRequestsReferences") or {}
        for value in references.get("nodes", []):
            if value.get("state") != "OPEN":
                continue
            names = [label["name"] for label in (value.get("labels") or {}).get("nodes", [])]
            if wanted({"labels": names}) and value["number"] not in found:
                found.append(value["number"])
    return found


def _release_pull_requests(repository: str, pulls: List[int]) -> List[str]:
    """Remove qew:draft from a pull request once none of the issues it closes is a draft."""
    notes = []
    for pull in pulls:
        try:
            fetched = fetch_pull_request_for_merge(repository, pull)["pull_request"]
            closing = fetched["closingIssuesReferences"]["nodes"]
            if any(
                normalize_labels([label["name"] for label in value["labels"]["nodes"]]).draft
                for value in closing
            ):
                continue
            edit_pull_request_label(repository, pull, "qew:draft", add=False)
        except (QewError, KeyError, TypeError) as error:
            notes.append(f"PR #{pull} still carries qew:draft ({error}); remove it with: gh pr edit {pull} --remove-label qew:draft")
    return notes


def _hold_pull_requests(repository: str, pulls: List[int]) -> List[str]:
    """Label a draft pull request qew:draft again after its issue went back to draft."""
    notes = []
    for pull in pulls:
        try:
            edit_pull_request_label(repository, pull, "qew:draft", add=True)
        except QewError as error:
            notes.append(f"PR #{pull} was not labelled qew:draft ({error}); add it with: gh pr edit {pull} --add-label qew:draft")
    return notes


def _roots(issues: List[int]) -> List[int]:
    if not issues or any(number <= 0 for number in issues):
        raise QewError("issue numbers must be positive", 2)
    return list(dict.fromkeys(issues))


def _apply(
    repository: str,
    targets: List[int],
    skipped: List[int],
    verb: str,
    write: Callable[[int], Optional[str]],
) -> Move:
    done: List[int] = []
    unrecorded: List[Tuple[int, str]] = []
    for number in targets:
        try:
            missing = write(number)
        except QewError as error:
            remaining = [value for value in targets if value not in done]
            raise QewError(
                f"{error}; {verb}: {_numbers(done)}; not {verb}: {_numbers(remaining)}; "
                "rerun the same command to finish, then run qew project ensure to refresh the board",
                3 if error.exit_code in (3, 4) else error.exit_code,
            ) from error
        done.append(number)
        if missing is not None:
            unrecorded.append((number, missing))
    return Move(done, skipped, tuple(unrecorded))


def _plan(
    repository: str, roots: List[int], tree: bool, selects: Selector, verb: str
) -> Planned:
    """The open issues to move, the ones passed over, and every node read.

    A root that does not qualify is skipped, not refused, so rerunning after a
    partial failure finishes the rest; only when nothing at all is left to move
    is that an error.
    """
    managed_only = load_config().managed_only
    queue: Deque[int] = deque(roots)
    seen: Set[int] = set(roots)
    targets: List[int] = []
    skipped: List[int] = []
    nodes: Dict[int, Dict[str, Any]] = {}
    while queue:
        number = queue.popleft()
        node = fetch_draft_node(repository, number)
        names = [label["name"] for label in node["labels"]["nodes"]]
        labels = normalize_labels(names)
        is_root = number in roots
        in_scope = labels.managed or not managed_only
        if is_root:
            if node.get("state") != "OPEN":
                raise QewError(f"#{number} is closed", 2)
            if not in_scope:
                raise QewError(f"#{number} is not managed by Qew", 2)
        if selects(labels) and in_scope:
            targets.append(number)
            nodes[number] = {**node, "labels_normalized": labels}
        else:
            skipped.append(number)
        if not tree:
            continue
        children = node.get("subIssues")
        if not isinstance(children, dict) or children.get("pageInfo", {}).get("hasNextPage") is not False:
            raise QewError(f"indeterminate: GitHub truncated the sub-issues of #{number}", 4)
        for child in children.get("nodes", []):
            if child.get("state") == "OPEN" and child["number"] not in seen:
                seen.add(child["number"])
                queue.append(child["number"])
                if len(seen) > TREE_LIMIT:
                    raise QewError(f"indeterminate: more than {TREE_LIMIT} issues under the roots", 4)
    if not targets:
        shown = ", ".join(f"#{value}" for value in skipped)
        reason = "not a draft" if verb == "approve" else "a draft or not approved work"
        raise QewError(f"nothing to {verb}: {shown} {'is' if len(skipped) == 1 else 'are'} {reason}", 2)
    return targets, skipped, nodes


def _require_idle(targets: List[int], nodes: Dict[int, Dict[str, Any]]) -> None:
    """Refuse the whole move when any target is claimed or already in review."""
    claimed = []
    reviewing = []
    for number in targets:
        node = nodes[number]
        labels = node["labels_normalized"]
        assignees = node.get("assignees")
        pulls = node.get("closedByPullRequestsReferences")
        if not isinstance(assignees, dict) or not isinstance(pulls, dict):
            raise QewError(f"indeterminate: no claim or pull request data for #{number}", 4)
        if pulls.get("pageInfo", {}).get("hasNextPage") is not False:
            raise QewError(f"indeterminate: truncated pull requests for #{number}", 4)
        logins = [value["login"] for value in assignees.get("nodes", [])]
        if labels.in_progress or assignees.get("totalCount") or logins:
            claimed.append((number, logins))
        # A GitHub draft pull request (a plan's) is not review; a ready one is.
        if any(
            value.get("state") == "OPEN" and value.get("isDraft") is not True
            for value in pulls.get("nodes", [])
        ):
            reviewing.append(number)
    if claimed:
        shown = "; ".join(
            f"#{number} is claimed" + (f" by {', '.join(logins)}" if logins else "")
            for number, logins in claimed
        )
        raise QewError(
            f"{shown}; release it first with qew release ISSUE --reason TEXT, nothing was changed", 3
        )
    if reviewing:
        raise QewError(
            f"{_numbers(reviewing)} has an open pull request; close or merge it first, nothing was changed", 2
        )


def _write(
    repository: str,
    number: int,
    arguments: List[str],
    comment: str,
    expected: Callable[[NormalizedLabels], bool],
) -> Optional[str]:
    """Move the labels, then record why; the comment text comes back if it failed.

    The labels are the truth, so a failed comment never undoes or hides the move:
    the caller is told the exact comment to add.
    """
    edit_issue(repository, number, arguments)
    missing: Optional[str] = None
    try:
        comment_issue(repository, number, comment)
    except QewError:
        missing = comment
    after = _labels(fetch_mutation_target(repository, number))
    if not expected(after):
        raise QewError(f"indeterminate: the labels of #{number} are not as written after the edit", 3)
    return missing


def _labels(payload: Dict[str, Any]) -> NormalizedLabels:
    issue = payload["data"]["repository"]["issue"]
    return normalize_labels([label["name"] for label in issue["labels"]["nodes"]])


def _numbers(values: List[int]) -> str:
    return ", ".join(f"#{value}" for value in values) or "none"
