"""Verified, idempotent completion of parent plans."""

from qew.errors import QewError
from qew.github import close_issue, fetch_issue, fetch_plan_status_graph
from qew.lifecycle import decode_work_state
from qew.model import normalize_labels
from qew.plan_status import derive_plan_statuses


def complete_plan(repository: str, issue: int, reason: str) -> bool:
    if issue <= 0:
        raise QewError("plan issue number must be positive", 2)
    if not reason.strip():
        raise QewError("reason must not be empty", 2)
    before = decode_work_state(fetch_issue(repository, issue))
    if before.number != issue:
        raise QewError(f"indeterminate: GitHub returned #{before.number} for #{issue}", 4)
    labels = normalize_labels(list(before.labels))
    if not labels.epic or len(labels.priorities) != 1:
        raise QewError(f"#{issue} is not a triaged plan", 2)
    if labels.draft:
        raise QewError(f"#{issue} is a draft plan; approve it first with qew draft approve {issue}", 2)
    if before.state == "CLOSED":
        if before.state_reason == "COMPLETED":
            return False
        raise QewError(f"#{issue} is closed without completed disposition", 2)
    if before.state != "OPEN":
        raise QewError(f"indeterminate: invalid state for #{issue}", 4)

    status = derive_plan_statuses(fetch_plan_status_graph(repository), issue)[0]
    children = status["children"]
    if children["total"] == 0:
        raise QewError(f"#{issue} has no children to complete", 2)
    remaining = children["total"] - children["closed"]
    if remaining:
        noun = "child remains" if remaining == 1 else "children remain"
        raise QewError(f"#{issue} cannot complete: {remaining} {noun} open", 2)

    close_issue(
        repository,
        issue,
        reason="completed",
        comment=reason,
        assignees=sorted(before.assignees),
    )
    after = decode_work_state(fetch_issue(repository, issue))
    if (
        after.number != issue
        or after.state != "CLOSED"
        or after.state_reason != "COMPLETED"
        or after.assignees
        or "in-progress" in after.labels
    ):
        raise QewError(f"plan completion verification failed for #{issue}", 3)
    return True
