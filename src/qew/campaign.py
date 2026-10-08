"""Idempotent milestone campaigns and plan assignment."""

from datetime import date
import json
import shlex
from typing import Any, Dict, List, Optional

from qew.errors import QewError
from qew.github import (
    assign_milestone,
    fetch_issue,
    create_milestone,
    fetch_issue_milestone,
    fetch_milestones,
    update_milestone,
)
from qew.lifecycle import decode_work_state
from qew.model import normalize_labels, text_findings
from qew.requester import require_quote, with_requested_by

TITLE_BYTES = 96


def create_campaign(
    repository: str,
    *,
    title: str,
    description: Optional[str],
    due_on: Optional[str],
    plans: List[int],
    quote: Optional[str],
) -> Dict[str, Any]:
    quote = require_quote(quote)
    if not title.strip():
        raise QewError("campaign title must not be empty", 2)
    if description is not None and not description.strip():
        raise QewError("campaign description must not be empty", 2)
    due_timestamp = _due_timestamp(due_on)
    if any(plan <= 0 for plan in plans):
        raise QewError("plan issue numbers must be positive", 2)
    if len(set(plans)) != len(plans):
        raise QewError("plan issue numbers must not repeat", 2)

    # Validate every plan before any write so a refusal leaves GitHub untouched.
    unassigned = [plan for plan in plans if not _plan_in_campaign(repository, plan, title)]

    matches = [item for item in fetch_milestones(repository) if item.get("title") == title]
    if len(matches) > 1:
        raise QewError(f"indeterminate: duplicate campaign title {title}", 4)
    changed = False
    if not matches:
        # Every milestone text Qew writes names the human who asked for it.
        milestone = create_milestone(
            repository,
            title=title,
            description=with_requested_by(description, quote),
            due_on=due_timestamp,
        )
        changed = True
    else:
        milestone = matches[0]
        if _text(milestone, "state") != "open":
            raise QewError(f"campaign {title} is closed", 2)
        recorded = with_requested_by(description, quote) if description is not None else None
        description_drift = recorded is not None and milestone.get("description") != recorded
        due_drift = due_timestamp is not None and milestone.get("due_on") != due_timestamp
        if description_drift or due_drift:
            milestone = update_milestone(
                repository,
                _integer(milestone, "number"),
                description=recorded if description_drift else None,
                due_on=due_timestamp if due_drift else None,
            )
            changed = True

    milestone_number = _integer(milestone, "number")
    for plan in unassigned:
        assign_milestone(repository, plan, title)
        verified = fetch_issue_milestone(repository, plan)
        if _integer(verified, "number") != plan:
            raise QewError(f"indeterminate: GitHub returned the wrong plan for #{plan}", 4)
        assigned = verified.get("milestone")
        if not isinstance(assigned, dict) or assigned.get("number") != milestone_number:
            raise QewError(f"campaign assignment verification failed for #{plan}", 3)
        changed = True

    return {
        "changed": changed,
        "milestone": milestone_number,
        "plans": plans,
        "schema_version": 1,
        "title": _text(milestone, "title"),
        "url": _text(milestone, "html_url"),
    }


def _plan_in_campaign(repository: str, plan: int, title: str) -> bool:
    """Require an open approved plan; report whether it already has this campaign."""

    work = decode_work_state(fetch_issue(repository, plan))
    if work.number != plan:
        raise QewError(f"indeterminate: GitHub returned #{work.number} for #{plan}", 4)
    labels = normalize_labels(list(work.labels))
    if work.state != "OPEN" or not labels.epic or not labels.approved or labels.draft:
        raise QewError(f"#{plan} is not an approved plan", 2)
    current = fetch_issue_milestone(repository, plan)
    if _integer(current, "number") != plan:
        raise QewError(f"indeterminate: GitHub returned the wrong plan for #{plan}", 4)
    assigned = current.get("milestone")
    if assigned is None:
        return False
    if not isinstance(assigned, dict) or not isinstance(assigned.get("title"), str):
        raise QewError(f"indeterminate: invalid milestone for #{plan}", 4)
    if assigned["title"] != title:
        raise QewError(f"#{plan} already belongs to campaign {assigned['title']!r}", 2)
    return True


def complete_campaign(repository: str, title: str) -> Dict[str, Any]:
    if not title.strip():
        raise QewError("campaign title must not be empty", 2)
    milestone = _unique_campaign(fetch_milestones(repository), title)
    number = _integer(milestone, "number")
    if _text(milestone, "state") == "closed":
        return _completion_result(milestone, False)
    open_issues = _integer(milestone, "open_issues")
    closed_issues = _integer(milestone, "closed_issues")
    if open_issues:
        noun = "issue" if open_issues == 1 else "issues"
        raise QewError(f"campaign {title} has {open_issues} open {noun}", 2)
    if closed_issues == 0:
        raise QewError(f"campaign {title} has no completed work", 2)

    updated = update_milestone(
        repository,
        number,
        description=None,
        due_on=None,
        state="closed",
    )
    if _integer(updated, "number") != number or _text(updated, "state") != "closed":
        raise QewError(f"campaign completion write failed for {title}", 3)
    verified = _unique_campaign(fetch_milestones(repository), title)
    if _integer(verified, "number") != number or _text(verified, "state") != "closed":
        raise QewError(f"campaign completion verification failed for {title}", 3)
    return _completion_result(verified, True)


def campaign_statuses(
    repository: str,
    title: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if title is not None and not title.strip():
        raise QewError("campaign title must not be empty", 2)
    milestones = fetch_milestones(repository)
    if title is None:
        selected = [item for item in milestones if item.get("state") == "open"]
    else:
        selected = [item for item in milestones if item.get("title") == title]
        if not selected:
            raise QewError(f"campaign not found: {title}", 2)
        if len(selected) > 1:
            raise QewError(f"indeterminate: duplicate campaign title {title}", 4)
    statuses = [_campaign_status(item) for item in selected]
    statuses.sort(key=lambda item: item["milestone"])
    return statuses


def _campaign_status(milestone: Dict[str, Any]) -> Dict[str, Any]:
    state = _text(milestone, "state")
    if state not in {"open", "closed"}:
        raise QewError("indeterminate: invalid milestone state", 4)
    open_issues = _integer(milestone, "open_issues")
    closed_issues = _integer(milestone, "closed_issues")
    if open_issues < 0 or closed_issues < 0:
        raise QewError("indeterminate: invalid milestone issue counts", 4)
    title = _text(milestone, "title")
    completable = state == "open" and open_issues == 0 and closed_issues > 0
    findings = sorted(text_findings(title))
    display = None if findings else _bounded_title(title)
    # The runnable command is emitted only for a title that needs no rewriting.
    runnable = completable and display == title
    status = {
        "closed_issues": closed_issues,
        "completable": completable,
        "complete_command": (
            f"qew campaign complete --title {shlex.quote(title)}" if runnable else None
        ),
        "milestone": _integer(milestone, "number"),
        "open_issues": open_issues,
        "schema_version": 1,
        "state": state,
        "title": display,
    }
    if findings:
        status["text_findings"] = findings
    return status


def render_campaign_title(status: Dict[str, Any]) -> str:
    """Quote a status title for text output, or name why it was withheld."""

    if status["title"] is None:
        return f"[title withheld: {', '.join(status['text_findings'])}]"
    return json.dumps(status["title"], ensure_ascii=False)


def _bounded_title(title: str) -> str:
    safe = " ".join(title.split())
    safe = "".join(character for character in safe if character.isprintable())
    encoded = safe.encode("utf-8")
    if len(encoded) <= TITLE_BYTES:
        return safe
    suffix = "…"
    body = encoded[: TITLE_BYTES - len(suffix.encode("utf-8"))].decode("utf-8", "ignore")
    return body + suffix


def _unique_campaign(milestones: List[Dict[str, Any]], title: str) -> Dict[str, Any]:
    matches = [item for item in milestones if item.get("title") == title]
    if not matches:
        raise QewError(f"campaign not found: {title}", 2)
    if len(matches) > 1:
        raise QewError(f"indeterminate: duplicate campaign title {title}", 4)
    return matches[0]


def _completion_result(milestone: Dict[str, Any], changed: bool) -> Dict[str, Any]:
    return {
        "changed": changed,
        "milestone": _integer(milestone, "number"),
        "schema_version": 1,
        "state": _text(milestone, "state"),
        "title": _text(milestone, "title"),
    }


def _due_timestamp(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise QewError("due-on must be YYYY-MM-DD", 2) from error
    return f"{parsed.isoformat()}T23:59:59Z"


def _text(value: Dict[str, Any], key: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise QewError(f"indeterminate: invalid milestone {key}", 4)
    return child


def _integer(value: Dict[str, Any], key: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid milestone {key}", 4)
    return child
