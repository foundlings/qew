"""Derive an epic's claim from its direct children.

While an open, non-epic direct child of an epic carries `in-progress`, the epic
carries `in-progress` too and is assigned to exactly those children's claimants.
When no child does, Qew removes the label and assignees it derived. Only direct
children count: blocked-by edges, grandchildren and child epics are linked all
over and would put every epic in progress. An epic with no claimed child and no
`in-progress` label of its own is never touched, so a human owner survives.
"""

from dataclasses import dataclass
import sys
from typing import Any, Dict, List, Optional, Set

from qew.errors import QewError
from qew.github import edit_issue, fetch_epic_claim
from qew.model import normalize_labels

CLAIM_LABEL = "in-progress"

# Epics whose claim this process changed, so the caller can sync their cards.
_changed: List[int] = []


@dataclass(frozen=True)
class EpicEdit:
    number: int
    add_assignees: List[str]
    remove_assignees: List[str]
    add_label: bool
    remove_label: bool

    def arguments(self) -> List[str]:
        arguments: List[str] = []
        if self.add_assignees:
            arguments.extend(("--add-assignee", ",".join(self.add_assignees)))
        if self.remove_assignees:
            arguments.extend(("--remove-assignee", ",".join(self.remove_assignees)))
        if self.add_label:
            arguments.extend(("--add-label", CLAIM_LABEL))
        if self.remove_label:
            arguments.extend(("--remove-label", CLAIM_LABEL))
        return arguments


def plan(issues: List[Dict[str, Any]]) -> List[EpicEdit]:
    """The edits that bring every open epic's claim in line with its direct children."""
    claimed: Set[int] = set()
    claimants: Dict[int, Set[str]] = {}
    for issue in issues:
        labels = _normalized(issue)
        if issue.get("state") != "OPEN" or labels.epic:
            continue
        parent = _parent(issue)
        if parent is None or not labels.in_progress:
            continue
        claimed.add(parent)
        claimants.setdefault(parent, set()).update(_logins(issue))
    edits = []
    for issue in issues:
        labels = _normalized(issue)
        if issue.get("state") != "OPEN" or not labels.epic or labels.draft:
            continue
        number = issue["number"]
        has_label = labels.in_progress
        if number not in claimed and not has_label:
            continue
        desired = claimants.get(number, set())
        current = set(_logins(issue))
        edit = EpicEdit(
            number=number,
            add_assignees=sorted(desired - current),
            remove_assignees=sorted(current - desired),
            add_label=number in claimed and not has_label,
            remove_label=number not in claimed and has_label,
        )
        if edit.arguments():
            edits.append(edit)
    return edits


def apply(repository: str, edits: List[EpicEdit]) -> None:
    for edit in edits:
        edit_issue(repository, edit.number, edit.arguments())


def apply_in_memory(issue: Dict[str, Any], edit: EpicEdit) -> None:
    """Mirror an applied edit into a fetched issue so later projection sees it."""
    labels = issue["labels"]["nodes"]
    if edit.add_label:
        labels.append({"name": CLAIM_LABEL})
    if edit.remove_label:
        issue["labels"]["nodes"] = [
            node for node in labels if str(node.get("name", "")).lower() != CLAIM_LABEL
        ]
    assignees = issue["assignees"]
    logins = (set(_logins(issue)) | set(edit.add_assignees)) - set(edit.remove_assignees)
    assignees["nodes"] = [{"login": login} for login in sorted(logins)]
    assignees["totalCount"] = len(logins)


def reconcile_parent(repository: str, epic: Optional[int]) -> bool:
    """Reconcile one epic after a child changed; a failure warns and never raises.

    Besides deriving its claim, an epic whose children are all closed is
    completed (see complete_plan), which also releases the claim.
    """
    if epic is None:
        return False
    try:
        payload = fetch_epic_claim(repository, epic)
        epic_issue = {**payload, "parent": None}
        nodes = payload["subIssues"]["nodes"]
        if _completable(epic_issue, nodes):
            from qew.plan_completion import complete_plan

            complete_plan(repository, epic, f"All {len(nodes)} child issues are closed.")
            _changed.append(epic)
            return True
        children = [{**child, "parent": {"number": epic}} for child in nodes]
        edits = plan([epic_issue, *children])
        apply(repository, edits)
    except (QewError, KeyError, TypeError) as error:
        print(f"warning: epic claim for #{epic} not updated: {error}", file=sys.stderr)
        return False
    if edits:
        _changed.append(epic)
    return bool(edits)


def _completable(epic: Dict[str, Any], children: List[Dict[str, Any]]) -> bool:
    """An open, triaged epic with children, every one closed."""
    labels = _normalized(epic)
    return (
        epic.get("state") == "OPEN"
        and labels.epic
        and not labels.draft
        and len(labels.priorities) == 1
        and bool(children)
        and all(child.get("state") == "CLOSED" for child in children)
    )


def take_changed() -> List[int]:
    changed = list(_changed)
    _changed.clear()
    return changed


def _normalized(issue: Dict[str, Any]):
    """The issue's labels through Qew's own rules (GitHub folds ASCII case)."""
    return normalize_labels([node["name"] for node in issue["labels"]["nodes"]])


def _parent(issue: Dict[str, Any]) -> Optional[int]:
    parent = issue.get("parent")
    number = parent.get("number") if isinstance(parent, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def _logins(issue: Dict[str, Any]) -> List[str]:
    assignees = issue["assignees"]
    nodes = assignees.get("nodes")
    if nodes is None:
        return []
    total = assignees.get("totalCount")
    if isinstance(total, int) and total > len(nodes):
        raise QewError(f"indeterminate: incomplete assignees for #{issue.get('number')}", 4)
    return [node["login"] for node in nodes]
