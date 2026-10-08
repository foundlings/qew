"""Merge a ready pull request, then land the issues it closes."""

from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional

from qew.checklist import open_items
from qew.errors import QewError
from qew.github import fetch_branch_rules, fetch_pull_request_for_merge, merge_pull_request
from qew.land import LandReport, land
from qew.model import normalize_labels
from qew.pr_owner import holds_every_claim


@dataclass(frozen=True)
class MergeReport:
    pull_request: int
    merged: bool
    auto: bool
    landing: Optional[LandReport]
    landing_error: Optional[str]
    author: str = ""


def merge(
    repository: str,
    number: int,
    *,
    auto: bool,
    admin: bool,
    reason: Optional[str],
    other_author: bool = False,
) -> MergeReport:
    if number <= 0:
        raise QewError("pull request number must be positive", 2)
    if admin and auto:
        raise QewError("--auto and --admin cannot be combined", 2)
    if admin and not (reason or "").strip():
        raise QewError("--admin requires --reason", 2)
    if other_author and not (reason or "").strip():
        raise QewError("--other-author requires --reason", 2)
    if reason is not None and not (admin or other_author):
        raise QewError("--reason is only for --admin or --other-author", 2)
    pull, head = _require_mergeable(
        repository, number, auto=auto, admin=admin, other_author=other_author
    )
    merge_pull_request(repository, number, head, auto=auto, admin=admin)
    after, _ = _read(repository, number)
    if after["state"] == "OPEN" and not isinstance(after.get("autoMergeRequest"), dict):
        # GitHub can lag a moment behind a merge it accepted; look once more.
        time.sleep(1.0)
        after, _ = _read(repository, number)
    if after["state"] == "MERGED":
        try:
            return MergeReport(number, True, auto, land(repository, number), None, pull["author"])
        except QewError as error:
            return MergeReport(number, True, auto, None, str(error), pull["author"])
    if auto and isinstance(after.get("autoMergeRequest"), dict):
        return MergeReport(number, False, True, None, None, pull["author"])
    raise QewError(
        f"PR #{number} is not merged and auto-merge is not enabled after the merge request", 3
    )


def _require_mergeable(
    repository: str, number: int, *, auto: bool, admin: bool, other_author: bool
):
    pull, viewer = _read(repository, number)
    state = pull["state"]
    if state != "OPEN":
        raise QewError(f"PR #{number} is {state.lower()}, not open", 2)
    author = pull["author"]
    closing = _closing(pull, number)
    if author != viewer and not other_author and not holds_every_claim([logins for _, _, logins in closing], viewer):
        raise QewError(
            f"PR #{number} was opened by {author}, not {viewer}; merge it either after taking over "
            f"the issues it closes (qew start ISSUE --take-over --reason TEXT) or with "
            f"--other-author --reason TEXT",
            3,
        )
    if pull["isDraft"]:
        raise QewError(f"PR #{number} is a draft; run qew pr ready {number} first", 2)
    if "qew:draft" in pull["labels"]:
        raise QewError(
            f"PR #{number} is labelled qew:draft: approve the issues it closes first "
            "(qew draft approve ISSUE --tree), which removes the label",
            2,
        )
    if pull["mergeable"] == "CONFLICTING":
        raise QewError(f"PR #{number} has merge conflicts", 2)
    boxes = open_items(pull["body"])
    if boxes:
        raise QewError(
            f"PR #{number} has open checklist items: {', '.join(item.id for item in boxes)}; "
            f"tick them with qew pr check {number}",
            2,
        )
    for issue, labels, _ in closing:
        if normalize_labels(labels).draft:
            raise QewError(f"PR #{number} closes #{issue}, which is a draft", 2)
    if not admin:
        _require_checks(repository, pull, number, auto)
    return pull, pull["headRefOid"]


def _require_checks(repository: str, pull: Dict[str, Any], number: int, auto: bool) -> None:
    rollup = pull["rollup"]
    if rollup in ("FAILURE", "ERROR"):
        raise QewError(f"PR #{number} checks are failing", 3)
    if auto:
        if not pull["protected"] and not _ruleset_requires_checks(repository, pull):
            raise QewError(
                f"the base branch of PR #{number} requires no status checks, so auto-merge would merge "
                "immediately; merge once the checks have passed, or use --admin with --reason",
                3,
            )
        return
    if rollup is not None and rollup != "SUCCESS":
        raise QewError(
            f"PR #{number} checks are {rollup.lower()}; wait for them, or use --auto where "
            "the base branch requires checks",
            3,
        )


def _ruleset_requires_checks(repository: str, pull: Dict[str, Any]) -> bool:
    """Whether an active ruleset (not a branch protection rule) requires status checks."""
    try:
        rules = fetch_branch_rules(repository, pull["baseRefName"])
    except QewError as error:
        if error.exit_code == 4:
            raise
        raise QewError(f"indeterminate: cannot read the rules of {pull['baseRefName']}: {error}", 4) from error
    return any(rule.get("type") == "required_status_checks" for rule in rules)


def _read(repository: str, number: int):
    fetched = fetch_pull_request_for_merge(repository, number)
    pull = fetched["pull_request"]
    viewer = fetched["viewer"]
    author = pull.get("author")
    login = author.get("login") if isinstance(author, dict) else None
    commits = _nodes(pull.get("commits"), "commits")
    rollup_value = None
    if commits:
        status = commits[0].get("commit", {}).get("statusCheckRollup")
        rollup_value = status.get("state") if isinstance(status, dict) else None
    base = pull.get("baseRef")
    rule = base.get("branchProtectionRule") if isinstance(base, dict) else None
    head = pull.get("headRefOid")
    if (
        pull.get("number") != number
        or pull.get("state") not in ("OPEN", "CLOSED", "MERGED")
        or not isinstance(pull.get("isDraft"), bool)
        or not isinstance(login, str)
        or not isinstance(viewer, str)
        or not isinstance(pull.get("body"), str)
        or not isinstance(head, str)
        or not head
    ):
        raise QewError(f"indeterminate: invalid data for PR #{number}", 4)
    return (
        {
            **pull,
            "author": login,
            "labels": [
                node.get("name")
                for node in (pull.get("labels") or {}).get("nodes", [])
                if isinstance(node, dict)
            ],
            "rollup": rollup_value,
            "protected": isinstance(rule, dict) and rule.get("requiresStatusChecks") is True,
        },
        viewer,
    )


def _closing(pull: Dict[str, Any], number: int) -> List[Any]:
    references = pull.get("closingIssuesReferences")
    if not isinstance(references, dict) or references.get("pageInfo", {}).get("hasNextPage") is not False:
        raise QewError("indeterminate: incomplete pagination in closingIssuesReferences", 4)
    found = []
    for node in _nodes(references, "closing issues"):
        labels = node.get("labels")
        if not isinstance(labels, dict) or labels.get("pageInfo", {}).get("hasNextPage") is not False:
            raise QewError(f"indeterminate: incomplete labels for #{node.get('number')}", 4)
        assignees = node.get("assignees")
        if not isinstance(assignees, dict):
            raise QewError(f"indeterminate: no assignees for #{node.get('number')}", 4)
        logins = [value["login"] for value in _nodes(assignees, "assignees")]
        if assignees.get("totalCount") != len(logins):
            raise QewError(f"indeterminate: incomplete assignees for #{node.get('number')}", 4)
        found.append(
            (node.get("number"), [label["name"] for label in _nodes(labels, "labels")], logins)
        )
    return found


def _ruleset_requires_checks(repository: str, pull: Dict[str, Any]) -> bool:
    """Whether an active ruleset (not a branch protection rule) requires status checks."""
    try:
        rules = fetch_branch_rules(repository, pull["baseRefName"])
    except QewError as error:
        if error.exit_code == 4:
            raise
        raise QewError(f"indeterminate: cannot read the rules of {pull['baseRefName']}: {error}", 4) from error
    return any(rule.get("type") == "required_status_checks" for rule in rules)


def _read(repository: str, number: int):
    fetched = fetch_pull_request_for_merge(repository, number)
    pull = fetched["pull_request"]
    viewer = fetched["viewer"]
    author = pull.get("author")
    login = author.get("login") if isinstance(author, dict) else None
    commits = _nodes(pull.get("commits"), "commits")
    rollup_value = None
    if commits:
        status = commits[0].get("commit", {}).get("statusCheckRollup")
        rollup_value = status.get("state") if isinstance(status, dict) else None
    base = pull.get("baseRef")
    rule = base.get("branchProtectionRule") if isinstance(base, dict) else None
    head = pull.get("headRefOid")
    if (
        pull.get("number") != number
        or pull.get("state") not in ("OPEN", "CLOSED", "MERGED")
        or not isinstance(pull.get("isDraft"), bool)
        or not isinstance(login, str)
        or not isinstance(viewer, str)
        or not isinstance(pull.get("body"), str)
        or not isinstance(head, str)
        or not head
    ):
        raise QewError(f"indeterminate: invalid data for PR #{number}", 4)
    return (
        {
            **pull,
            "author": login,
            "labels": [
                node.get("name")
                for node in (pull.get("labels") or {}).get("nodes", [])
                if isinstance(node, dict)
            ],
            "rollup": rollup_value,
            "protected": isinstance(rule, dict) and rule.get("requiresStatusChecks") is True,
        },
        viewer,
    )


def _closing(pull: Dict[str, Any], number: int) -> List[Any]:
    references = pull.get("closingIssuesReferences")
    if not isinstance(references, dict) or references.get("pageInfo", {}).get("hasNextPage") is not False:
        raise QewError("indeterminate: incomplete pagination in closingIssuesReferences", 4)
    found = []
    for node in _nodes(references, "closing issues"):
        labels = node.get("labels")
        if not isinstance(labels, dict) or labels.get("pageInfo", {}).get("hasNextPage") is not False:
            raise QewError(f"indeterminate: incomplete labels for #{node.get('number')}", 4)
        assignees = node.get("assignees")
        if not isinstance(assignees, dict):
            raise QewError(f"indeterminate: no assignees for #{node.get('number')}", 4)
        logins = [value["login"] for value in _nodes(assignees, "assignees")]
        if assignees.get("totalCount") != len(logins):
            raise QewError(f"indeterminate: incomplete assignees for #{node.get('number')}", 4)
        found.append(
            (node.get("number"), [label["name"] for label in _nodes(labels, "labels")], logins)
        )
    return found


def _holds_every_claim(closing: List[Any], viewer: str) -> bool:
    """True when the pull request closes something and the caller is assigned to all of it."""
    return bool(closing) and all(viewer in logins for _, _, logins in closing)


def _nodes(connection: Any, context: str) -> List[Dict[str, Any]]:
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid {context}", 4)
    return nodes
