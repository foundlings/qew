"""Who may act on a pull request: its author, or the login holding every claim it closes."""

from typing import Any, Dict, List

from qew.errors import QewError


def claim_holders(pull: Dict[str, Any]) -> List[List[str]]:
    """The assignee logins of each issue the pull request closes (empty when unread)."""
    references = pull.get("closingIssuesReferences")
    if not isinstance(references, dict):
        return []
    page_info = references.get("pageInfo")
    if isinstance(page_info, dict) and page_info.get("hasNextPage") is not False:
        raise QewError("indeterminate: incomplete pagination in closingIssuesReferences", 4)
    holders = []
    for node in references.get("nodes", []):
        assignees = node.get("assignees") if isinstance(node, dict) else None
        if not isinstance(assignees, dict):
            return []
        logins = [value["login"] for value in assignees.get("nodes", []) if isinstance(value, dict)]
        if assignees.get("totalCount") != len(logins):
            raise QewError(f"indeterminate: incomplete assignees for #{node.get('number')}", 4)
        holders.append(logins)
    return holders


def holds_every_claim(holders: List[List[str]], viewer: str) -> bool:
    """True when the pull request closes something and the caller is assigned to all of it."""
    return bool(holders) and all(viewer in logins for logins in holders)


def require_owner(number: int, author: str, viewer: str, pull: Dict[str, Any]) -> None:
    if author == viewer or holds_every_claim(claim_holders(pull), viewer):
        return
    raise QewError(
        f"PR #{number} was opened by {author}, not {viewer}; act on it after taking over the issues "
        "it closes (qew start ISSUE --take-over --reason TEXT)",
        3,
    )
