"""Backfill author labels on issues opened before Qew labelled them."""

from typing import Any, Dict, List, Optional, Set, Tuple

from qew.config import load_config
from qew.errors import QewError
from qew.model import AUTHOR_LABEL_PREFIX
from qew.github import (
    AUTHOR_LABEL_COLOR,
    LABEL_NAME_LIMIT,
    _LOGIN,
    edit_issue,
    fetch_authored_issues,
    fetch_repository_labels,
    upsert_label,
)


def backfill(repository: str, dry_run: bool = False) -> Dict[str, Any]:
    """Add author:LOGIN to every in-scope issue without one, from GitHub's recorded opener.

    In overlay mode only Qew-managed issues are in scope. The login is the one
    GitHub records for the issue, never anything a caller supplies.
    """
    plan, skipped = _plan(fetch_authored_issues(repository))
    result: Dict[str, Any] = {
        "created_labels": [],
        "dry_run": dry_run,
        "labelled": sorted(plan),
        "schema_version": 1,
        "skipped": skipped,
    }
    if not plan:
        return result
    existing = {_name(label).casefold() for label in fetch_repository_labels(repository)}
    wanted = sorted({label for label in plan.values()}, key=str.casefold)
    result["created_labels"] = [label for label in wanted if label.casefold() not in existing]
    if dry_run:
        return result
    for label in result["created_labels"]:
        login = label[len(AUTHOR_LABEL_PREFIX):]
        upsert_label(
            repository,
            name=label,
            color=AUTHOR_LABEL_COLOR,
            description=f"Opened through Qew by {login}",
            force=False,
        )
    for number in sorted(plan):
        edit_issue(repository, number, ["--add-label", plan[number]])
    remaining, _ = _plan(fetch_authored_issues(repository))
    unresolved = sorted(set(plan) & set(remaining))
    if unresolved:
        shown = ", ".join(f"#{number}" for number in unresolved)
        raise QewError(f"indeterminate: author labels did not persist on {shown}", 4)
    return result


def _plan(issues: List[Dict[str, Any]]) -> Tuple[Dict[int, str], List[Dict[str, Any]]]:
    managed_only = load_config().managed_only
    plan: Dict[int, str] = {}
    skipped: List[Dict[str, Any]] = []
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            raise QewError("indeterminate: invalid issue number", 4)
        names = _label_names(issue, number)
        if managed_only and "qew:managed" not in names:
            continue
        if any(name.casefold().startswith(AUTHOR_LABEL_PREFIX) for name in names):
            continue
        author = issue.get("author")
        if author is None:
            skipped.append({"issue": number, "reason": "the opener's account no longer exists"})
            continue
        login = author.get("login") if isinstance(author, dict) else None
        label = f"{AUTHOR_LABEL_PREFIX}{login}" if isinstance(login, str) else ""
        if not isinstance(login, str) or not _LOGIN.fullmatch(login) or len(label) > LABEL_NAME_LIMIT:
            raise QewError(f"indeterminate: invalid opener login for #{number}", 4)
        plan[number] = label
    return plan, skipped


def _label_names(issue: Dict[str, Any], number: int) -> Set[str]:
    connection = issue.get("labels")
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list):
        raise QewError(f"indeterminate: invalid labels for #{number}", 4)
    names = set()
    for node in nodes:
        name = node.get("name") if isinstance(node, dict) else None
        if not isinstance(name, str):
            raise QewError(f"indeterminate: invalid label for #{number}", 4)
        names.add(name)
    return names


def _name(label: Dict[str, Any]) -> str:
    name = label.get("name")
    if not isinstance(name, str):
        raise QewError("indeterminate: invalid repository label", 4)
    return name
