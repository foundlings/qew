"""Complete a merged pull request's closing issues for the caller."""

from dataclasses import dataclass
import re
from typing import Any, Dict, List

from qew.errors import QewError
from qew.github import fetch_issue, fetch_pull_request
from qew.lifecycle import LandedCommit, complete, decode_work_state


_FULL_COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class LandReport:
    pull_request: int
    merged: bool
    landed: List[int]
    clean: List[int]
    open_issues: List[int]
    held: Dict[int, List[str]]


def land(repository: str, pull_request: int) -> LandReport:
    """Clear the caller's stale claims on the issues a merged PR closed.

    Closing issues come from GitHub's own references, never from body text. Work
    held by another login is reported, not touched: taking it over is deliberate.
    """
    if pull_request <= 0:
        raise QewError("pull request number must be positive", 2)
    pull = fetch_pull_request(repository, pull_request)
    number = pull.get("number")
    if number != pull_request:
        raise QewError(f"indeterminate: GitHub returned PR {number!r} for #{pull_request}", 4)
    state = pull.get("state")
    if state not in ("OPEN", "CLOSED", "MERGED"):
        raise QewError(f"indeterminate: invalid state for PR #{pull_request}", 4)
    if state != "MERGED":
        return LandReport(pull_request, False, [], [], [], {})
    base = pull.get("baseRefName")
    merge = pull.get("mergeCommit")
    commit = merge.get("oid") if isinstance(merge, dict) else None
    if not isinstance(base, str) or not base:
        raise QewError(f"indeterminate: invalid base branch for PR #{pull_request}", 4)
    if not isinstance(commit, str) or not _FULL_COMMIT.fullmatch(commit):
        raise QewError(f"indeterminate: invalid merge commit for PR #{pull_request}", 4)
    issues = _closing_issues(pull, pull_request)

    report = LandReport(pull_request, True, [], [], [], {})
    for issue in issues:
        work = decode_work_state(fetch_issue(repository, issue))
        if work.number != issue:
            raise QewError(f"indeterminate: GitHub returned #{work.number} for #{issue}", 4)
        if work.state != "CLOSED":
            report.open_issues.append(issue)
            continue
        others = work.assignees - {work.viewer}
        if others:
            report.held[issue] = sorted(others)
            continue
        if not work.assignees and "in-progress" not in work.labels:
            report.clean.append(issue)
            continue
        complete(
            repository,
            issue,
            f"Landed via PR #{pull_request}.",
            LandedCommit(commit, base),
        )
        report.landed.append(issue)
    return report


def _closing_issues(pull: Dict[str, Any], pull_request: int) -> List[int]:
    references = pull.get("closingIssuesReferences")
    page_info = references.get("pageInfo") if isinstance(references, dict) else None
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not False:
        raise QewError("indeterminate: incomplete pagination in closingIssuesReferences", 4)
    nodes = references.get("nodes")
    if not isinstance(nodes, list):
        raise QewError(f"indeterminate: invalid closing issues for PR #{pull_request}", 4)
    numbers = []
    for node in nodes:
        value = node.get("number") if isinstance(node, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise QewError(f"indeterminate: invalid closing issue for PR #{pull_request}", 4)
        numbers.append(value)
    return sorted(set(numbers))


def render(report: LandReport) -> List[str]:
    """The human lines describing a land report, shared by the command and the hook."""
    lines = []
    if not report.merged:
        lines.append(f"PR #{report.pull_request} is not merged; nothing to land")
    for issue in report.landed:
        lines.append(f"landed #{issue} from PR #{report.pull_request}")
    for issue, owners in sorted(report.held.items()):
        lines.append(
            f"#{issue} is held by {', '.join(owners)}; to finish it deliberately run: "
            f"qew complete {issue} --take-over --reason TEXT "
            "(--commit SHA --landed-on BRANCH | --no-commit REASON)"
        )
    for issue in report.open_issues:
        lines.append(f"#{issue} is still open; PR #{report.pull_request} did not close it")
    if report.merged and not report.landed and not report.held and not report.open_issues:
        lines.append("nothing to land")
    return lines
