"""Typed work creation and canonical issue-body composition."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from qew.errors import QewError
from qew.github import create_issue
from qew.model import reject_reserved_areas
from qew.requester import require_quote, with_requested_by


@dataclass(frozen=True)
class CreateRequest:
    repository: str
    issue_type: str
    title: str
    priority: str
    areas: List[str]
    parent: Optional[int]
    blocked_by: List[int]
    discovered_from: Optional[int]
    where: Optional[str]
    problem: Optional[str]
    goal: Optional[str]
    fix: Optional[str]
    approach: Optional[str]
    done_when: List[str]
    body_file: Optional[str]
    quote: Optional[str]
    needs_approval: bool
    body_text: Optional[str] = None
    provenance_marker: Optional[str] = None
    draft: bool = False


def create(request: CreateRequest, advisory: Optional[Callable[[str], None]] = None):
    body = compose_body(request)
    if advisory is not None:
        advisory(body)
    return create_issue(
        request.repository,
        title=request.title,
        issue_type=request.issue_type,
        priority=request.priority,
        areas=request.areas,
        body=body,
        parent=request.parent,
        blocked_by=request.blocked_by,
        approved=not request.draft,
        needs_approval=request.needs_approval and not request.draft,
        draft=request.draft,
    )


def compose_body(request: CreateRequest) -> str:
    quote = require_quote(request.quote)
    _validate(request)
    body = with_requested_by(_body(request), quote)
    if request.provenance_marker is not None:
        body = f"{body}\n\n{request.provenance_marker}" if body else request.provenance_marker
    return body


def _validate(request: CreateRequest) -> None:
    if not request.title.strip():
        raise QewError("title must not be empty", 2)
    reject_reserved_areas(request.areas)
    if request.problem is not None and request.goal is not None:
        raise QewError("problem and goal are mutually exclusive", 2)
    if request.fix is not None and request.approach is not None:
        raise QewError("fix and approach are mutually exclusive", 2)
    sections = [
        request.where,
        request.problem,
        request.goal,
        request.fix,
        request.approach,
        *request.done_when,
    ]
    body_sources = sum(
        (
            request.body_file is not None,
            request.body_text is not None,
            any(value is not None for value in sections),
        )
    )
    if body_sources > 1:
        raise QewError("body, body-file, and structured body fields are mutually exclusive", 2)
    numbers = [*request.blocked_by]
    if request.parent is not None:
        numbers.append(request.parent)
    if request.discovered_from is not None:
        numbers.append(request.discovered_from)
    if any(number <= 0 for number in numbers):
        raise QewError("issue numbers must be positive", 2)


def _body(request: CreateRequest) -> str:
    if request.body_text is not None:
        if not request.body_text.strip():
            raise QewError("body must not be empty", 2)
        body = request.body_text
    elif request.body_file is not None:
        path = Path(request.body_file)
        if path.is_symlink():
            raise QewError("body-file must not be a symlink", 2)
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"cannot read body-file: {error}", 2) from error
        if not body.strip():
            raise QewError("body-file must not be empty", 2)
    else:
        sections = []
        _section(sections, "Where", request.where)
        _section(sections, "Problem", request.problem)
        _section(sections, "Goal", request.goal)
        _section(sections, "Fix", request.fix)
        _section(sections, "Approach", request.approach)
        if request.done_when:
            checklist = "\n".join(f"- [ ] {item}" for item in request.done_when)
            _section(sections, "Done when", checklist)
        body = "\n\n".join(sections)
    if request.discovered_from is not None:
        provenance = f"Discovered while working on #{request.discovered_from}."
        body = f"{body}\n\n{provenance}" if body else provenance
    return body


def _section(sections: List[str], heading: str, value: Optional[str]) -> None:
    if value is not None:
        if not value.strip():
            raise QewError(f"{heading.lower()} must not be empty", 2)
        sections.append(f"### {heading}\n\n{value}")
