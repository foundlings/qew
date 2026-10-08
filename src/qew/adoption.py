"""Adopt Qew-created issues that never received qew:managed."""

import re
from typing import Any, Dict, List, Optional

from qew.errors import QewError
from qew.github import _LOGIN, edit_issue, fetch_approved_issues

# The provenance marker `qew apply` writes into every issue it creates, alone on a line.
_MARKER = re.compile(r"^<!-- qew:apply [0-9a-f]{64}:[0-9a-f]{16} -->$", re.MULTILINE)
MANAGED = "qew:managed"


def adopt(repository: str, author: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Add qew:managed to approved issues carrying Qew's apply marker but not the label.

    Pre-Qew issues have no marker and are never touched; a marker only counts on
    an issue a human already approved with qew:approved. `author` limits the scope
    to one opener.
    """
    if author is not None and not _LOGIN.fullmatch(author):
        raise QewError("--author must be a GitHub login", 2)
    plan = _plan(fetch_approved_issues(repository), author)
    result: Dict[str, Any] = {"adopted": plan, "dry_run": dry_run, "schema_version": 1}
    if dry_run or not plan:
        return result
    for number in plan:
        edit_issue(repository, number, ["--add-label", MANAGED])
    remaining = set(_plan(fetch_approved_issues(repository), author))
    unresolved = sorted(remaining & set(plan))
    if unresolved:
        shown = ", ".join(f"#{number}" for number in unresolved)
        raise QewError(f"indeterminate: qew:managed did not persist on {shown}", 4)
    return result


def _plan(issues: List[Dict[str, Any]], author: Optional[str]) -> List[int]:
    plan: List[int] = []
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            raise QewError("indeterminate: invalid issue number", 4)
        names = _label_names(issue, number)
        if "qew:approved" not in names or MANAGED in names:
            continue
        body = issue.get("body")
        if not isinstance(body, str) or not _MARKER.search(body.replace("\r\n", "\n")):
            continue
        if author is not None:
            opener = issue.get("author")
            login = opener.get("login") if isinstance(opener, dict) else None
            if not isinstance(login, str) or login.casefold() != author.casefold():
                continue
        plan.append(number)
    return sorted(plan)


def _label_names(issue: Dict[str, Any], number: int) -> set:
    connection = issue.get("labels")
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list):
        raise QewError(f"indeterminate: invalid labels for #{number}", 4)
    names = set()
    for node in nodes:
        name = node.get("name") if isinstance(node, dict) else None
        if not isinstance(name, str):
            raise QewError(f"indeterminate: invalid label for #{number}", 4)
        names.add(name.lower())
    return names
