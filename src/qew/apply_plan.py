"""Validated, checkpointed application of JSONL work plans."""

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from qew.errors import QewError
from qew.campaign import create_campaign
from qew.github import (
    fetch_dependency_graph,
    fetch_issue_body,
    fetch_issue_milestone,
    fetch_organization_projects,
    fetch_project_items,
    find_issues_by_marker,
    set_parent,
)
from qew.graph import block
from qew.project import ensure_project
from qew.requester import require_quote
from qew.work import CreateRequest, compose_body, create


Reference = Union[int, str]
ALLOWED_FIELDS = {
    "id",
    "title",
    "type",
    "priority",
    "areas",
    "parent",
    "blocked-by",
    "discovered-from",
    "where",
    "problem",
    "goal",
    "fix",
    "approach",
    "done-when",
    "body",
    "campaign",
    "project",
    "quote",
    "approved",
}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
THROTTLE_PATTERN = re.compile(r"^(0|[0-9]+(?:\.[0-9]+)?(?:ms|s))$")


@dataclass(frozen=True)
class PlanEntry:
    key: str
    request: CreateRequest
    parent: Optional[Reference]
    blockers: List[Reference]
    campaign: Optional["CampaignSpec"]
    project: Optional[str]


@dataclass(frozen=True)
class CampaignSpec:
    title: str
    description: Optional[str]
    due_on: Optional[str]


@dataclass(frozen=True)
class Plan:
    digest: str
    entries: List[PlanEntry]


@dataclass
class MutationPacer:
    seconds: float
    sleep: Callable[[float], None] = time.sleep
    mutation_seen: bool = False

    def before_write(self) -> None:
        if self.mutation_seen and self.seconds > 0:
            self.sleep(self.seconds)
        self.mutation_seen = True


def execute(
    repository: str,
    plan_path: str,
    state_path: Optional[str],
    dry_run: bool,
    throttle_seconds: float = 0.5,
    draft: bool = False,
) -> Dict[str, Any]:
    plan = load_plan(Path(plan_path), repository, draft)
    checkpoint_path = Path(state_path) if state_path else Path(f"{plan_path}.state.json")
    operations = _operations(plan)
    if dry_run:
        if checkpoint_path.exists():
            _load_checkpoint(checkpoint_path, repository, plan)
        return {
            "operations": operations,
            "plan_sha256": plan.digest,
            "schema_version": 1,
        }

    pacer = MutationPacer(throttle_seconds)
    state = _load_checkpoint(checkpoint_path, repository, plan)
    for entry in plan.entries:
        if entry.key in state["created"]:
            continue
        marker = _marker(plan.digest, entry.key)
        request = _with_marker(entry.request, marker)
        created = _adopt_marked_issue(repository, entry, marker)
        if created is None:
            pacer.before_write()
            created = create(request)
        state["created"][entry.key] = {**created, "marker": marker}
        _save_checkpoint(checkpoint_path, state)

    for entry in plan.entries:
        if entry.parent is None:
            continue
        operation = f"{entry.key}:{_reference_key(entry.parent)}"
        if operation in state["parents"]:
            continue
        pacer.before_write()
        set_parent(
            repository,
            _created_number(state, entry.key),
            _resolve_reference(state, entry.parent),
        )
        state["parents"].append(operation)
        _save_checkpoint(checkpoint_path, state)

    for entry in plan.entries:
        for blocker in entry.blockers:
            operation = f"{entry.key}:{_reference_key(blocker)}"
            if operation in state["blockers"]:
                continue
            pacer.before_write()
            block(
                repository,
                _created_number(state, entry.key),
                _resolve_reference(state, blocker),
            )
            state["blockers"].append(operation)
            _save_checkpoint(checkpoint_path, state)

    for entry in plan.entries:
        if entry.campaign is None:
            continue
        operation = f"{entry.key}:{entry.campaign.title}"
        if operation in state["campaigns"]:
            continue
        pacer.before_write()
        result = create_campaign(
            repository,
            title=entry.campaign.title,
            description=entry.campaign.description,
            due_on=entry.campaign.due_on,
            plans=[_created_number(state, entry.key)],
            quote=entry.request.quote,
        )
        state["campaigns"][operation] = result["milestone"]
        _save_checkpoint(checkpoint_path, state)

    for title in _project_titles(plan):
        if title in state["projects"]:
            continue
        pacer.before_write()
        result = ensure_project(repository, title)
        state["projects"][title] = result["project"]
        _save_checkpoint(checkpoint_path, state)

    return {
        "campaigns": dict(sorted(state["campaigns"].items())),
        "created": {
            key: value["number"] for key, value in sorted(state["created"].items())
        },
        "plan_sha256": plan.digest,
        "projects": dict(sorted(state["projects"].items())),
        "schema_version": 1,
    }


def _adopt_marked_issue(
    repository: str, entry: PlanEntry, marker: str
) -> Optional[Dict[str, Any]]:
    """Recover an issue created before its checkpoint was saved."""
    matches = [
        issue
        for issue in find_issues_by_marker(repository, marker)
        if issue["title"] == entry.request.title
    ]
    if len(matches) > 1:
        numbers = ", ".join(f"#{issue['number']}" for issue in matches)
        raise QewError(f"ambiguous provenance marker for {entry.key}: {numbers}", 2)
    if not matches:
        return None
    return {"number": matches[0]["number"], "url": matches[0]["url"]}


def parse_throttle(value: str) -> float:
    if THROTTLE_PATTERN.fullmatch(value) is None:
        raise QewError("throttle must be 0 or a duration in ms or s", 2)
    if value == "0":
        return 0.0
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    return float(value[:-1])


def load_plan(path: Path, repository: str, draft: bool = False) -> Plan:
    if path.is_symlink():
        raise QewError("plan file must not be a symlink", 2)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise QewError(f"cannot read plan: {error}", 2) from error
    digest = hashlib.sha256(raw).hexdigest()
    entries = []
    identifiers: Set[str] = set()
    try:
        plan_text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QewError("plan must be UTF-8", 2) from error
    for line_number, raw_line in enumerate(plan_text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise QewError(f"invalid JSON on line {line_number}: {error.msg}", 2) from error
        if not isinstance(value, dict):
            raise QewError(f"plan line {line_number} must be an object", 2)
        unknown = sorted(set(value) - ALLOWED_FIELDS)
        if unknown:
            raise QewError(f"unknown field on line {line_number}: {unknown[0]}", 2)
        identifier = value.get("id")
        if identifier is not None:
            if not isinstance(identifier, str) or ID_PATTERN.fullmatch(identifier) is None:
                raise QewError(f"invalid id on line {line_number}", 2)
        key = identifier or f"line-{line_number}"
        if key in identifiers:
            raise QewError(f"duplicate id: {key}", 2)
        identifiers.add(key)
        entries.append(_entry(value, key, repository, line_number, draft))
    if not entries:
        raise QewError("plan must contain at least one work item", 2)
    plan = Plan(digest=digest, entries=entries)
    _validate_references(plan)
    return plan


def _entry(
    value: Dict[str, Any], key: str, repository: str, line: int, draft: bool = False
) -> PlanEntry:
    title = _required_text(value, "title", line)
    issue_type = _required_text(value, "type", line)
    if issue_type not in {"bug", "enhancement", "task", "epic"}:
        raise QewError(f"invalid type on line {line}: {issue_type}", 2)
    priority = value.get("priority", "P2")
    if priority not in {"P0", "P1", "P2", "P3", "P4"}:
        raise QewError(f"invalid priority on line {line}", 2)
    areas = _text_list(value.get("areas", []), "areas", line)
    done_when = _text_list(value.get("done-when", []), "done-when", line)
    blockers_raw = value.get("blocked-by", [])
    if not isinstance(blockers_raw, list):
        raise QewError(f"blocked-by must be a list on line {line}", 2)
    blockers = [_reference(item, "blocked-by", line) for item in blockers_raw]
    parent = value.get("parent")
    if parent is not None:
        parent = _reference(parent, "parent", line)
    discovered = value.get("discovered-from")
    if discovered is not None and (not isinstance(discovered, int) or isinstance(discovered, bool) or discovered <= 0):
        raise QewError(f"discovered-from must be a positive issue number on line {line}", 2)
    quote_value = value.get("quote")
    if quote_value is not None and not isinstance(quote_value, str):
        raise QewError(f"quote must be text on line {line}", 2)
    quote = require_quote(quote_value, f"quote field on line {line}")
    approved = value.get("approved", False)
    if not isinstance(approved, bool):
        raise QewError(f"approved must be true or false on line {line}", 2)
    if approved and draft:
        raise QewError(f"approved on line {line} contradicts --draft: drafts are approved with qew draft approve", 2)
    if approved and issue_type == "epic":
        raise QewError(f"approved applies only to work packages, not a plan, on line {line}", 2)
    request = CreateRequest(
        repository=repository,
        issue_type=issue_type,
        title=title,
        priority=priority,
        areas=areas,
        parent=None,
        blocked_by=[],
        discovered_from=discovered,
        where=_optional_text(value, "where", line),
        problem=_optional_text(value, "problem", line),
        goal=_optional_text(value, "goal", line),
        fix=_optional_text(value, "fix", line),
        approach=_optional_text(value, "approach", line),
        done_when=done_when,
        body_file=None,
        quote=quote,
        # A plan is never started, so it carries the quote but no approval gate.
        needs_approval=issue_type != "epic" and not approved and not draft,
        body_text=_optional_text(value, "body", line),
        draft=draft,
    )
    compose_body(request)
    campaign = _campaign(value.get("campaign"), line)
    project = _optional_text(value, "project", line)
    if project is not None and not project.strip():
        raise QewError(f"project must be non-empty text on line {line}", 2)
    if (campaign is not None or project is not None) and issue_type != "epic":
        raise QewError(f"campaign and project require an epic on line {line}", 2)
    return PlanEntry(
        key=key,
        request=request,
        parent=parent,
        blockers=blockers,
        campaign=campaign,
        project=project,
    )


def _validate_references(plan: Plan) -> None:
    keys = {entry.key for entry in plan.entries}
    dependency_graph: Dict[str, Set[str]] = {entry.key: set() for entry in plan.entries}
    parent_graph: Dict[str, Set[str]] = {entry.key: set() for entry in plan.entries}
    for entry in plan.entries:
        if isinstance(entry.parent, str):
            if entry.parent not in keys:
                raise QewError(f"dangling parent reference: {entry.parent}", 2)
            parent_graph[entry.key].add(entry.parent)
        for blocker in entry.blockers:
            if isinstance(blocker, str):
                if blocker not in keys:
                    raise QewError(f"dangling blocker reference: {blocker}", 2)
                dependency_graph[entry.key].add(blocker)
    _reject_cycle(dependency_graph, "dependency")
    _reject_cycle(parent_graph, "parent")


def _reject_cycle(graph: Dict[str, Set[str]], kind: str) -> None:
    visiting: Set[str] = set()
    visited: Set[str] = set()
    for root in graph:
        if root in visited:
            continue
        visiting.add(root)
        path = [root]
        stack = [iter(graph[root])]
        while stack:
            adjacent = next(stack[-1], None)
            if adjacent is None:
                stack.pop()
                node = path.pop()
                visiting.remove(node)
                visited.add(node)
            elif adjacent in visiting:
                raise QewError(f"{kind} cycle includes {adjacent}", 2)
            elif adjacent not in visited:
                visiting.add(adjacent)
                path.append(adjacent)
                stack.append(iter(graph[adjacent]))


def _operations(plan: Plan) -> List[Dict[str, Any]]:
    operations = [
        {
            "action": "create",
            "id": entry.key,
            "title": entry.request.title,
            "type": entry.request.issue_type,
            **({"draft": True} if entry.request.draft else {}),
        }
        for entry in plan.entries
    ]
    for entry in plan.entries:
        if entry.parent is not None:
            operations.append(
                {"action": "parent", "issue": entry.key, "parent": entry.parent}
            )
    for entry in plan.entries:
        for blocker in entry.blockers:
            operations.append(
                {"action": "block", "issue": entry.key, "blocked_by": blocker}
            )
    for entry in plan.entries:
        if entry.campaign is not None:
            operations.append(
                {
                    "action": "campaign",
                    "issue": entry.key,
                    "title": entry.campaign.title,
                }
            )
    for title in _project_titles(plan):
        operations.append({"action": "project", "title": title})
    return operations


def _load_checkpoint(path: Path, repository: str, plan: Plan) -> Dict[str, Any]:
    if not path.exists():
        return {
            "blockers": [],
            "campaigns": {},
            "created": {},
            "parents": [],
            "plan_sha256": plan.digest,
            "projects": {},
            "repository": repository,
            "schema_version": 1,
        }
    if path.is_symlink():
        raise QewError("state file must not be a symlink", 2)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QewError(f"cannot read state file: {error}", 2) from error
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise QewError("state file has an unsupported schema", 2)
    if state.get("repository") != repository:
        raise QewError("state file belongs to another repository", 2)
    if state.get("plan_sha256") != plan.digest:
        raise QewError("state file belongs to another plan revision", 2)
    created = state.get("created")
    if not isinstance(created, dict):
        raise QewError("state file has invalid created work", 2)
    keys = {entry.key for entry in plan.entries}
    for key, value in created.items():
        if key not in keys or not isinstance(value, dict):
            raise QewError("state file references unknown work", 2)
        number = value.get("number")
        marker = value.get("marker")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or not isinstance(marker, str)
        ):
            raise QewError("state file has invalid work provenance", 2)
        if marker != _marker(plan.digest, key):
            raise QewError(f"state provenance mismatch for #{number}", 2)
        if marker not in fetch_issue_body(repository, number):
            raise QewError(f"state provenance mismatch for #{number}", 2)
    if not isinstance(state.get("parents"), list) or not isinstance(state.get("blockers"), list):
        raise QewError("state file has invalid completed operations", 2)
    campaigns = state.setdefault("campaigns", {})
    projects = state.setdefault("projects", {})
    if not isinstance(campaigns, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, int)
        or isinstance(value, bool)
        for key, value in campaigns.items()
    ):
        raise QewError("state file has invalid campaigns", 2)
    if not isinstance(projects, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, int)
        or isinstance(value, bool)
        for key, value in projects.items()
    ):
        raise QewError("state file has invalid projects", 2)
    expected_campaigns = {
        f"{entry.key}:{entry.campaign.title}"
        for entry in plan.entries
        if entry.campaign is not None
    }
    if any(key not in expected_campaigns for key in campaigns):
        raise QewError("state file references an unknown campaign", 2)
    expected_projects = set(_project_titles(plan))
    if any(key not in expected_projects for key in projects):
        raise QewError("state file references an unknown project", 2)
    _verify_completed_operations(repository, plan, state)
    return state


def _verify_completed_operations(
    repository: str,
    plan: Plan,
    state: Dict[str, Any],
) -> None:
    expected_parents = {
        f"{entry.key}:{_reference_key(entry.parent)}": entry
        for entry in plan.entries
        if entry.parent is not None
    }
    expected_blockers = {
        f"{entry.key}:{_reference_key(blocker)}": (entry, blocker)
        for entry in plan.entries
        for blocker in entry.blockers
    }
    parents = _checkpoint_keys(state["parents"], expected_parents, "parent")
    blockers = _checkpoint_keys(state["blockers"], expected_blockers, "blocker")
    if parents or blockers:
        issues = _graph_index(fetch_dependency_graph(repository))
        for operation in parents:
            entry = expected_parents[operation]
            issue_number = _created_number(state, entry.key)
            expected_parent = _resolve_reference(state, entry.parent)
            issue = _required_graph_issue(issues, issue_number)
            parent = issue.get("parent")
            actual_parent = None
            if parent is not None:
                if not isinstance(parent, dict):
                    raise QewError(f"indeterminate: invalid parent for #{issue_number}", 4)
                actual_parent = _positive_integer(parent.get("number"), "parent number")
            if actual_parent != expected_parent:
                raise QewError(f"parent checkpoint mismatch for #{issue_number}", 2)
        for operation in blockers:
            entry, blocker = expected_blockers[operation]
            issue_number = _created_number(state, entry.key)
            expected_blocker = _resolve_reference(state, blocker)
            issue = _required_graph_issue(issues, issue_number)
            blocked_by = issue.get("blockedBy")
            if not isinstance(blocked_by, dict) or not isinstance(blocked_by.get("nodes"), list):
                raise QewError(f"indeterminate: invalid blockers for #{issue_number}", 4)
            actual_blockers = {
                _positive_integer(item.get("number"), "blocker number")
                for item in blocked_by["nodes"]
                if isinstance(item, dict)
            }
            if expected_blocker not in actual_blockers:
                raise QewError(f"blocker checkpoint mismatch for #{issue_number}", 2)

    campaigns = state["campaigns"]
    expected_campaigns = {
        f"{entry.key}:{entry.campaign.title}": entry
        for entry in plan.entries
        if entry.campaign is not None
    }
    for operation, milestone_number in campaigns.items():
        entry = expected_campaigns[operation]
        issue_number = _created_number(state, entry.key)
        verified = fetch_issue_milestone(repository, issue_number)
        if verified.get("number") != issue_number:
            raise QewError(f"indeterminate: GitHub returned the wrong issue for #{issue_number}", 4)
        milestone = verified.get("milestone")
        if (
            not isinstance(milestone, dict)
            or milestone.get("number") != milestone_number
            or milestone.get("title") != entry.campaign.title
        ):
            raise QewError(f"campaign checkpoint mismatch for #{issue_number}", 2)

    if state["projects"]:
        inventory = fetch_organization_projects(repository)
        inventory_by_number = {
            project.get("number"): project
            for project in inventory["projects"]
            if isinstance(project, dict)
        }
        created_numbers = {
            _positive_integer(value.get("number"), "created issue number")
            for value in state["created"].values()
        }
        for title, project_number in state["projects"].items():
            project = inventory_by_number.get(project_number)
            if not isinstance(project, dict) or project.get("title") != title:
                raise QewError(f"project checkpoint mismatch for {title}", 2)
            projected_numbers = set()
            for item in fetch_project_items(repository, project_number):
                content = item.get("content")
                if isinstance(content, dict) and content.get("__typename") == "Issue":
                    projected_numbers.add(
                        _positive_integer(content.get("number"), "projected issue number")
                    )
            if not created_numbers.issubset(projected_numbers):
                raise QewError(f"project checkpoint missing created work for {title}", 2)


def _checkpoint_keys(values: List[Any], expected: Dict[str, Any], kind: str) -> List[str]:
    if any(not isinstance(value, str) for value in values):
        raise QewError(f"state file has invalid {kind} operations", 2)
    if len(set(values)) != len(values) or any(value not in expected for value in values):
        raise QewError(f"state file references an unknown {kind} operation", 2)
    return values


def _graph_index(payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    data = payload.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    issues = repository.get("issues") if isinstance(repository, dict) else None
    nodes = issues.get("nodes") if isinstance(issues, dict) else None
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError("indeterminate: invalid dependency graph", 4)
    return {
        _positive_integer(issue.get("number"), "graph issue number"): issue
        for issue in nodes
    }


def _required_graph_issue(
    issues: Dict[int, Dict[str, Any]], number: int
) -> Dict[str, Any]:
    issue = issues.get(number)
    if issue is None:
        raise QewError(f"checkpoint issue #{number} is missing from GitHub", 2)
    return issue


def _positive_integer(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise QewError(f"indeterminate: invalid {context}", 4)
    return value


def _save_checkpoint(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(state, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _with_marker(request: CreateRequest, marker: str) -> CreateRequest:
    values = dict(request.__dict__)
    values["provenance_marker"] = marker
    return CreateRequest(**values)


def _marker(digest: str, key: str) -> str:
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"<!-- qew:apply {digest}:{key_digest} -->"


def _created_number(state: Dict[str, Any], key: str) -> int:
    created = state["created"].get(key)
    if not isinstance(created, dict):
        raise QewError(f"state file is missing created work for {key}", 2)
    return _positive_integer(created.get("number"), "created issue number")


def _resolve_reference(state: Dict[str, Any], reference: Reference) -> int:
    return reference if isinstance(reference, int) else _created_number(state, reference)


def _reference_key(reference: Reference) -> str:
    return str(reference)


def _project_titles(plan: Plan) -> List[str]:
    titles = []
    for entry in plan.entries:
        if entry.project is not None and entry.project not in titles:
            titles.append(entry.project)
    return titles


def _campaign(value: Any, line: int) -> Optional[CampaignSpec]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise QewError(f"campaign must be an object on line {line}", 2)
    unknown = sorted(set(value) - {"title", "description", "due-on"})
    if unknown:
        raise QewError(f"unknown campaign field on line {line}: {unknown[0]}", 2)
    title = _required_text(value, "title", line)
    description = _optional_text(value, "description", line)
    if description is not None and not description.strip():
        raise QewError(f"campaign description must not be empty on line {line}", 2)
    due_on = _optional_text(value, "due-on", line)
    if due_on is not None:
        try:
            date.fromisoformat(due_on)
        except ValueError as error:
            raise QewError(f"campaign due-on must be YYYY-MM-DD on line {line}", 2) from error
    return CampaignSpec(title=title, description=description, due_on=due_on)


def _required_text(value: Dict[str, Any], key: str, line: int) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise QewError(f"{key} must be non-empty text on line {line}", 2)
    return result


def _optional_text(value: Dict[str, Any], key: str, line: int) -> Optional[str]:
    result = value.get(key)
    if result is not None and not isinstance(result, str):
        raise QewError(f"{key} must be text on line {line}", 2)
    return result


def _text_list(value: Any, key: str, line: int) -> List[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise QewError(f"{key} must be a list of non-empty text on line {line}", 2)
    return value


def _reference(value: Any, key: str, line: int) -> Reference:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise QewError(f"{key} has an invalid reference on line {line}", 2)
    if isinstance(value, int) and value <= 0:
        raise QewError(f"{key} has a non-positive issue number on line {line}", 2)
    if isinstance(value, str) and ID_PATTERN.fullmatch(value) is None:
        raise QewError(f"{key} has an invalid id on line {line}", 2)
    return value
