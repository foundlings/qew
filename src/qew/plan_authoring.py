"""Native creation of approved parent plans."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from qew.errors import QewError
from qew.github import fetch_issue_parent, set_parent
from qew.work import CreateRequest, create


@dataclass(frozen=True)
class PlanCreateRequest:
    repository: str
    title: str
    priority: str
    children: List[int]
    goal: Optional[str]
    done_when: List[str]
    body_file: Optional[str]
    body_text: Optional[str]
    quote: Optional[str]
    draft: bool = False


def create_plan(request: PlanCreateRequest) -> Dict[str, Any]:
    if request.draft and request.children:
        raise QewError("a draft plan takes no --children; file them with create --draft --parent N", 2)
    _validate_children(request.children)
    created = create(
        CreateRequest(
            repository=request.repository,
            issue_type="epic",
            title=request.title,
            priority=request.priority,
            areas=[],
            parent=None,
            blocked_by=[],
            discovered_from=None,
            where=None,
            problem=None,
            goal=request.goal,
            fix=None,
            approach=None,
            done_when=request.done_when,
            body_file=request.body_file,
            quote=request.quote,
            # A plan is never started, so it carries the quote but no approval gate.
            needs_approval=False,
            body_text=request.body_text,
            draft=request.draft,
        )
    )
    attached: List[int] = []
    for child in request.children:
        try:
            if child == created["number"]:
                raise QewError("a plan cannot be its own child", 2)
            set_parent(request.repository, child, created["number"])
            if fetch_issue_parent(request.repository, child) != created["number"]:
                raise QewError(f"parent verification failed for #{child}", 3)
        except QewError as error:
            raise QewError(
                f"{error}; partial completion: created plan #{created['number']}; "
                f"attached: {_format_issues(attached)}; "
                f"not attached: {_format_issues(request.children[len(attached):])}; "
                "do not rerun plan create",
                error.exit_code,
            ) from error
        attached.append(child)
    return {**created, "children": request.children}


def _validate_children(children: List[int]) -> None:
    if any(child <= 0 for child in children):
        raise QewError("child issue numbers must be positive", 2)
    if len(set(children)) != len(children):
        raise QewError("child issue numbers must be unique", 2)


def _format_issues(numbers: List[int]) -> str:
    return ", ".join(f"#{number}" for number in numbers) or "none"
