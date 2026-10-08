"""Idempotent GitHub Projects visibility projection."""

from dataclasses import dataclass
import os
import re
import shlex
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from qew.config import linked_project, load_config
from qew.epic_claims import apply as apply_epic_claims
from qew.epic_claims import apply_in_memory as apply_epic_edit_in_memory
from qew.epic_claims import plan as plan_epic_claims
from qew.errors import QewError
from qew.vcs import checkout_repository
from qew.github import (
    delete_project_item,
    add_project_item,
    clear_project_item_field,
    create_organization_project,
    create_project_field,
    create_project_view,
    fetch_organization_projects,
    fetch_project_items,
    fetch_project_structure,
    fetch_projection_issue,
    fetch_projection_issues,
    update_project_field,
    update_project_item_field,
    update_project_view,
)
from qew.model import normalize_labels


@dataclass(frozen=True)
class OptionSpec:
    name: str
    color: str
    description: str


# Status options Qew used to own: the old option becomes the new one in place (keeping
# its id and cards), or is dropped when the new one already exists.
LEGACY_STATUS_OPTIONS = {"To triage": "Draft"}

STATUS_OPTIONS = (
    OptionSpec("Draft", "GRAY", "Not workable yet: drafts, and work missing approval, a type or a priority"),
    OptionSpec("Backlog", "GREEN", "Approved but blocked, deferred, or a planning container"),
    OptionSpec("Ready", "BLUE", "Qew reports this work as actionable now"),
    OptionSpec("Needs approval", "PINK", "Waiting for a human to authorize the work before it may start"),
    OptionSpec("In progress", "YELLOW", "Claimed and actively being implemented"),
    OptionSpec("In review", "PURPLE", "Implementation has an open linked pull request"),
    OptionSpec("Done", "ORANGE", "Closed and completed"),
)

PRIORITY_OPTIONS = tuple(
    OptionSpec(f"P{number}", color, description)
    for number, color, description in (
        (0, "RED", "Critical priority"),
        (1, "ORANGE", "High priority"),
        (2, "YELLOW", "Normal priority"),
        (3, "GREEN", "Low priority"),
        (4, "GRAY", "Backlog priority"),
    )
)

MANAGED_FILTER = "label:qew:managed"

VIEWS = (
    ("Kanban", "BOARD_LAYOUT", None),
    ("Table", "TABLE_LAYOUT", None),
    ("Ready", "TABLE_LAYOUT", "status:Ready"),
    ("Blocked", "TABLE_LAYOUT", "is:blocked"),
    ("Active", "TABLE_LAYOUT", 'status:"In progress"'),
    ("Review", "TABLE_LAYOUT", 'status:"In review"'),
    ("Drafts", "TABLE_LAYOUT", "status:Draft"),
    ("Roadmap", "ROADMAP_LAYOUT", None),
)

VERIFICATION_RETRY_DELAYS = (0.5, 1.5, 3.0, 5.0, 10.0)
# GitHub links a new PR's closing reference asynchronously; a few seconds suffice.
# QEW_LINK_RETRY_SCALE shortens the waits (the walks set it to 0); it never lengthens them.
LINK_RETRY_DELAYS = (0.5, 1.0, 2.0)


def _select_project(
    inventory: Dict[str, Any], title: Optional[str], repository: str
) -> Optional[Dict[str, Any]]:
    """Pick the Project linked by number in .qew/config.json, else the one titled `title`.

    With a link, `title` may only confirm the board's current name. Without one,
    a missing titled Project is None so the caller may create it.
    """
    projects = inventory["projects"]
    linked = linked_project(repository)
    if linked is not None:
        matches = [project for project in projects if project.get("number") == linked]
        if not matches:
            raise QewError(f"no project numbered {linked}", 2)
        actual = _text(matches[0], "title", "project")
        if title is not None and title != actual:
            raise QewError(
                f"--title {title} conflicts with the linked project {linked} ({actual})", 2
            )
        return matches[0]
    matches = [project for project in projects if project.get("title") == title]
    if len(matches) > 1:
        raise QewError(f"indeterminate: duplicate project title {title}", 4)
    return matches[0] if matches else None


def _require_selector(title: Optional[str], repository: str) -> None:
    """Refuse before any call when neither a link nor a usable --title exists."""
    if title is not None and not title.strip():
        raise QewError("project title must not be empty", 2)
    if title is None and linked_project(repository) is None:
        if load_config().project is not None:
            raise QewError(
                f"this checkout's board link belongs to {checkout_repository()}, not {repository}; "
                "pass --title TITLE to select that repository's board",
                2,
            )
        raise QewError(
            "no project: pass --title TITLE or link one with qew init --project NUMBER", 2
        )


def ensure_project(repository: str, title: Optional[str]) -> Dict[str, Any]:
    _require_selector(title, repository)
    inventory = fetch_organization_projects(repository)
    changed = []
    project = _select_project(inventory, title, repository)
    if project is None:
        owner_id = inventory.get("owner_id")
        if not isinstance(owner_id, str):
            raise QewError("indeterminate: invalid organization identity", 4)
        project = create_organization_project(owner_id, title)
        changed.append("project")
    project_id = _text(project, "id", "project")
    project_number = _integer(project, "number", "project")

    structure = fetch_project_structure(repository, project_number)
    status_field, status_changed = _ensure_field(
        structure,
        project_id,
        "Status",
        STATUS_OPTIONS,
        LEGACY_STATUS_OPTIONS,
    )
    priority_field, priority_changed = _ensure_field(
        structure,
        project_id,
        "Priority",
        PRIORITY_OPTIONS,
    )
    if status_changed:
        changed.append("field:Status")
    if priority_changed:
        changed.append("field:Priority")
    views_changed = _ensure_views(structure, project_id)
    changed.extend(f"view:{name}" for name in views_changed)
    if status_changed or priority_changed or views_changed:
        structure = fetch_project_structure(repository, project_number)
        status_field = _single_field(structure, "Status")
        priority_field = _single_field(structure, "Priority")

    items = _item_index(fetch_project_items(repository, project_number), repository)
    issues = fetch_projection_issues(repository)
    by_number = {_integer(issue, "number", "issue"): issue for issue in issues}
    epic_edits = plan_epic_claims(issues)
    apply_epic_claims(repository, epic_edits)
    for edit in epic_edits:
        apply_epic_edit_in_memory(by_number[edit.number], edit)
        changed.append(f"epic:#{edit.number}")
    status_options = _option_ids(status_field)
    priority_options = _option_ids(priority_field)
    item_changes = False
    for issue in sorted(issues, key=lambda value: _integer(value, "number", "issue")):
        number = _integer(issue, "number", "issue")
        item = items.get(number)
        if item is None:
            item_id = add_project_item(project_id, _text(issue, "id", f"issue #{number}"))
            item = {"id": item_id, "values": {}}
            items[number] = item
            changed.append(f"item:#{number}")
            item_changes = True
        writes = _write_item(
            project_id,
            item,
            number,
            _projection(issue),
            (priority_field, status_field, priority_options, status_options),
        )
        changed.extend(writes)
        item_changes = item_changes or bool(writes)

    if item_changes:
        _verify_projection_with_retry(
            repository,
            project_number,
            issues,
        )
    return {
        "changed": changed,
        "project": project_number,
        "schema_version": 1,
        "title": _text(project, "title", "project"),
        "url": _text(project, "url", "project"),
    }


def _write_item(
    project_id: str,
    item: Dict[str, Any],
    number: int,
    desired: Tuple[Optional[str], str],
    fields: Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str], Dict[str, str]],
) -> List[str]:
    """Write only the Priority and Status values that differ; return what changed."""
    desired_priority, desired_status = desired
    priority_field, status_field, priority_options, status_options = fields
    written = []
    if item["values"].get("Priority") != desired_priority:
        if desired_priority is None:
            clear_project_item_field(project_id, item["id"], _text(priority_field, "id", "Priority"))
        else:
            update_project_item_field(
                project_id,
                item["id"],
                _text(priority_field, "id", "Priority"),
                _required_option(priority_options, desired_priority, "Priority"),
            )
        written.append(f"priority:#{number}")
    if item["values"].get("Status") != desired_status:
        update_project_item_field(
            project_id,
            item["id"],
            _text(status_field, "id", "Status"),
            _required_option(status_options, desired_status, "Status"),
        )
        written.append(f"status:#{number}")
    return written


def sync_cards(
    repository: str,
    number: int,
    issues: List[int],
    dependents: bool = False,
    awaited_pull_requests: Optional[Dict[int, int]] = None,
) -> Dict[str, Any]:
    """Move the cards of just-mutated issues without a complete Project pass.

    Targeted and unverified: it fetches each affected issue with its Project
    items, adds a missing card, and writes only differing values. `dependents`
    also refreshes the issues the targets block, whose readiness may change.
    `awaited_pull_requests` maps an issue to the PR just created for it: GitHub
    links that closing reference asynchronously, so the issue is re-read until
    the PR appears, and a reference that never appears is reported, not guessed.
    """
    awaited = dict(awaited_pull_requests or {})
    inventory = fetch_organization_projects(repository)
    matches = [project for project in inventory["projects"] if project.get("number") == number]
    if not matches:
        raise QewError(f"no project numbered {number}", 2)
    title = _text(matches[0], "title", "project")
    project_id = _text(matches[0], "id", "project")
    structure = fetch_project_structure(repository, number)
    status_field = _single_field(structure, "Status")
    priority_field = _single_field(structure, "Priority")
    fields = (
        priority_field,
        status_field,
        _option_ids(priority_field),
        _option_ids(status_field),
    )
    managed_only = load_config().managed_only
    targets = list(dict.fromkeys(issues))
    queue = list(targets)
    seen = set()
    synced = []
    while queue:
        issue_number = queue.pop(0)
        if issue_number in seen:
            continue
        seen.add(issue_number)
        issue = fetch_projection_issue(repository, issue_number)
        if issue_number in awaited:
            issue = _await_pull_request(repository, issue_number, awaited[issue_number], issue)
        if dependents and issue_number in targets:
            queue.extend(
                _integer(node, "number", "dependent")
                for node in _connection_nodes(issue, "blocking", issue_number)
            )
        labels = [
            _text(label, "name", "label")
            for label in _connection_nodes(issue, "labels", issue_number)
        ]
        if managed_only and not normalize_labels(labels).managed:
            continue
        item = _board_item(issue, number, issue_number)
        if item is None:
            item = {
                "id": add_project_item(project_id, _text(issue, "id", f"issue #{issue_number}")),
                "values": {},
            }
        _write_item(project_id, item, issue_number, _projection(issue), fields)
        synced.append(issue_number)
    return {"issues": synced, "project": number, "title": title}


def _await_pull_request(
    repository: str,
    issue_number: int,
    pull_request: int,
    issue: Dict[str, Any],
) -> Dict[str, Any]:
    """Re-read the issue until GitHub reports `pull_request` as a closing reference.

    Deliberately bounded (three waits): each re-read fetches the whole projection
    issue, so this must never become an open-ended poll.
    """
    scale = _link_retry_scale()
    for delay in (*LINK_RETRY_DELAYS, None):
        linked = {
            node.get("number")
            for node in _connection_nodes(issue, "closedByPullRequestsReferences", issue_number)
        }
        if pull_request in linked:
            return issue
        if delay is None:
            break
        time.sleep(delay * scale)
        issue = fetch_projection_issue(repository, issue_number)
    print(
        f"warning: GitHub has not linked PR #{pull_request} to #{issue_number} yet; "
        f"its card keeps the derived status; run qew project ensure once GitHub shows the link.",
        file=sys.stderr,
    )
    return issue


def _link_retry_scale() -> float:
    try:
        value = float(os.environ.get("QEW_LINK_RETRY_SCALE", "1"))
    except ValueError:
        return 1.0
    return min(max(value, 0.0), 1.0)


def _board_item(issue: Dict[str, Any], project_number: int, number: int) -> Optional[Dict[str, Any]]:
    for node in _connection_nodes(issue, "projectItems", number):
        project = _mapping(node, "project", "project item")
        if _integer(project, "number", "project item project") == project_number:
            return {"id": _text(node, "id", "project item"), "values": _item_values(node, number)}
    return None


def _ensure_field(
    structure: Dict[str, Any],
    project_id: str,
    name: str,
    specs: Tuple[OptionSpec, ...],
    legacy: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], bool]:
    matches = _fields(structure, name)
    if len(matches) > 1:
        raise QewError(f"indeterminate: duplicate project field {name}", 4)
    if not matches:
        return create_project_field(project_id, name, _new_options(specs)), True
    field = matches[0]
    existing = field.get("options")
    if not isinstance(existing, list) or any(not isinstance(option, dict) for option in existing):
        raise QewError(f"indeterminate: invalid options for project field {name}", 4)
    existing, retired = _retire_legacy(existing, legacy)
    options = _new_options(specs)
    owned = {spec.name for spec in specs}
    # Options Qew does not own belong to humans; keep them, with their ids.
    options.extend(_foreign_option(option, name) for option in existing if option.get("name") not in owned)
    if not retired and [option.get("name") for option in existing] == [option["name"] for option in options]:
        return field, False
    ids = {
        option.get("name"): option.get("id")
        for option in existing
        if isinstance(option.get("name"), str) and isinstance(option.get("id"), str)
    }
    for option in options:
        if option["name"] in ids:
            option["id"] = ids[option["name"]]
    return update_project_field(_text(field, "id", name), options), True


def _retire_legacy(options: List[Dict[str, Any]], legacy: Optional[Dict[str, str]]):
    """Rename an option Qew no longer owns to its replacement, or drop it when that exists."""
    retired = False
    for old, new in (legacy or {}).items():
        names = [option.get("name") for option in options]
        if old not in names:
            continue
        retired = True
        if new in names:
            options = [option for option in options if option.get("name") != old]
        else:
            options = [{**option, "name": new} if option.get("name") == old else option for option in options]
    return options, retired


def _foreign_option(option: Dict[str, Any], field: str) -> Dict[str, str]:
    result = {}
    for key in ("id", "name", "color", "description"):
        result[key] = _text(option, key, f"project field {field} option")
    return result


def _view_specs() -> Tuple[Tuple[str, str, Optional[str]], ...]:
    """Scope Qew's views to managed issues when the repository is an overlay."""
    if not load_config().managed_only:
        return VIEWS
    return tuple(
        (name, layout, " ".join(filter(None, (MANAGED_FILTER, filter_value))))
        for name, layout, filter_value in VIEWS
    )


def _ensure_views(structure: Dict[str, Any], project_id: str) -> List[str]:
    connection = _mapping(structure, "views", "project")
    views = _nodes(connection, "project views")
    changed = []
    if not views:
        raise QewError("indeterminate: project has no default view", 4)
    default = min(views, key=lambda value: _integer(value, "number", "view"))
    if default.get("name") != "Kanban" or default.get("layout") != "BOARD_LAYOUT":
        update_project_view(
            _text(default, "id", "default view"),
            name="Kanban",
            layout="BOARD_LAYOUT",
        )
        default = {**default, "name": "Kanban", "layout": "BOARD_LAYOUT"}
        changed.append("Kanban")
    by_name = {view.get("name"): view for view in views if view is not default}
    by_name["Kanban"] = default
    for name, layout, filter_value in _view_specs():
        view = by_name.get(name)
        if view is None:
            view = create_project_view(project_id, name, layout)
            if filter_value is not None:
                view = update_project_view(
                    _text(view, "id", f"view {name}"),
                    filter_value=filter_value,
                )
            changed.append(name)
            continue
        mismatch = view.get("layout") != layout
        filter_mismatch = filter_value is not None and view.get("filter") != filter_value
        if mismatch or filter_mismatch:
            update_project_view(
                _text(view, "id", f"view {name}"),
                layout=layout if mismatch else None,
                filter_value=filter_value if filter_mismatch else None,
            )
            changed.append(name)
    return changed


def _projection(issue: Dict[str, Any]) -> Tuple[Optional[str], str]:
    number = _integer(issue, "number", "issue")
    labels = [
        _text(label, "name", "label")
        for label in _connection_nodes(issue, "labels", number)
    ]
    normalized = normalize_labels(labels)
    priority = None if normalized.priority is None else normalized.priority.value
    state = _text(issue, "state", f"issue #{number}")
    if state == "CLOSED":
        return priority, "Done"
    if state != "OPEN":
        raise QewError(f"indeterminate: invalid state for #{number}", 4)
    if normalized.draft:
        # Held back before every other rule, even when qew:approved is also present.
        return priority, "Draft"
    if normalized.epic:
        # An epic is in progress only while Qew derives a claim for it from a
        # claimed direct child (see epic_claims).
        return priority, "In progress" if normalized.in_progress else "Backlog"
    if not normalized.approved or not normalized.triaged:
        return priority, "Draft"
    if normalized.needs_approval:
        # Outranks claim, review and blockers: stuck work gated for a human shows
        # where a human looks.
        return priority, "Needs approval"
    pull_requests = _connection_nodes(issue, "closedByPullRequestsReferences", number)
    if any(_text(item, "state", "pull request") == "OPEN" for item in pull_requests):
        return priority, "In review"
    assignees = _mapping(issue, "assignees", f"issue #{number}")
    assignee_count = _integer(assignees, "totalCount", "assignees")
    if normalized.in_progress or assignee_count > 0:
        return priority, "In progress"
    blockers = _connection_nodes(issue, "blockedBy", number)
    if all(_text(blocker, "state", "blocker") == "CLOSED" for blocker in blockers):
        return priority, "Ready"
    return priority, "Backlog"


def _verify_projection_with_retry(
    repository: str,
    project_number: int,
    issues: List[Dict[str, Any]],
) -> None:
    attempts = len(VERIFICATION_RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        failures = _projection_failures(
            fetch_project_items(repository, project_number),
            issues,
            repository,
        )
        if not failures:
            return
        if attempt == attempts:
            raise QewError(f"project verification failed: {', '.join(failures)}", 3)
        print(
            f"warning: project verification attempt {attempt}/{attempts} failed: "
            f"{', '.join(failures)}; retrying",
            file=sys.stderr,
        )
        time.sleep(VERIFICATION_RETRY_DELAYS[attempt - 1])


def _projection_failures(
    items: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
    repository: str,
) -> List[str]:
    return [f"{kind} #{number}" for kind, number in _drift(items, issues, repository)]


def _drift(
    items: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
    repository: str,
) -> List[Tuple[str, int]]:
    """Return (kind, issue number) where the Project differs from the projection."""
    indexed = _item_index(items, repository)
    drift = []
    for issue in issues:
        number = _integer(issue, "number", "issue")
        item = indexed.get(number)
        if item is None:
            drift.append(("item", number))
            continue
        priority, status = _projection(issue)
        if item["values"].get("Status") != status:
            drift.append(("status", number))
        if item["values"].get("Priority") != priority:
            drift.append(("priority", number))
    return drift


def _has_stale_claim(issue: Dict[str, Any]) -> bool:
    """A closed issue that still carries an in-progress label or an assignee."""
    number = _integer(issue, "number", "issue")
    if _text(issue, "state", f"issue #{number}") != "CLOSED":
        return False
    labels = [
        _text(label, "name", "label") for label in _connection_nodes(issue, "labels", number)
    ]
    assignees = _mapping(issue, "assignees", f"issue #{number}")
    return normalize_labels(labels).in_progress or _integer(assignees, "totalCount", "assignees") > 0


CLAIM_FIX = (
    "qew land PR for the pull request that closed each one, or "
    "qew complete ISSUE --reason TEXT (--commit SHA --landed-on BRANCH | --no-commit REASON), "
    "adding --take-over when another login holds it"
)


_OWNER_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def prune_project(repository: str, source: str, title: Optional[str]) -> Dict[str, Any]:
    """Delete every card on the board whose issue belongs to `source`.

    The deliberate way to remove cards another repository's run put on this
    board. Only an exact repository match is deleted, and never the board's own.
    """
    if not _OWNER_REPOSITORY.fullmatch(source):
        raise QewError("--from must be OWNER/REPO", 2)
    if source.casefold() == repository.casefold():
        raise QewError(f"refusing to prune {source}: it is this board's own repository", 2)
    _require_selector(title, repository)
    project = _select_project(fetch_organization_projects(repository), title, repository)
    if project is None:
        raise QewError(f"no project titled {title}", 2)
    project_id = _text(project, "id", "project")
    project_number = _integer(project, "number", "project")
    victims = _cards_of(fetch_project_items(repository, project_number), source)
    for item_id in victims:
        delete_project_item(project_id, item_id)
    if victims and _cards_of(fetch_project_items(repository, project_number), source):
        raise QewError(f"indeterminate: cards of {source} remain after pruning", 4)
    return {
        "from": source,
        "project": project_number,
        "removed": len(victims),
        "schema_version": 1,
        "title": _text(project, "title", "project"),
    }


def _cards_of(items: List[Dict[str, Any]], source: str) -> List[str]:
    ids: List[str] = []
    for item in items:
        content = item.get("content")
        if not isinstance(content, dict) or content.get("__typename") != "Issue":
            continue
        owner = content.get("repository")
        name = owner.get("nameWithOwner") if isinstance(owner, dict) else None
        if isinstance(name, str) and name.casefold() == source.casefold():
            item_id = _text(item, "id", "project item")
            if item_id not in ids:
                ids.append(item_id)
    return ids


def project_status(repository: str, title: Optional[str]) -> Dict[str, Any]:
    """Report Project drift without writing anything."""
    _require_selector(title, repository)
    project = _select_project(fetch_organization_projects(repository), title, repository)
    if project is None:
        raise QewError(
            f"no project titled {title}; create it with: qew project ensure --title {shlex.quote(title)}",
            2,
        )
    project_number = _integer(project, "number", "project")
    project_title = _text(project, "title", "project")
    items = fetch_project_items(repository, project_number)
    issues = fetch_projection_issues(repository)
    drift = _drift(items, issues, repository)
    stale_epic_claims = sorted(edit.number for edit in plan_epic_claims(issues))
    stale_claims = sorted(
        _integer(issue, "number", "issue") for issue in issues if _has_stale_claim(issue)
    )
    missing = sorted(number for kind, number in drift if kind == "item")
    stale_status = sorted(number for kind, number in drift if kind == "status")
    stale_priority = sorted(number for kind, number in drift if kind == "priority")
    in_sync = not drift and not stale_epic_claims
    return {
        "claim_fix": CLAIM_FIX if stale_claims else None,
        "fix": None if in_sync else f"qew project ensure --title {shlex.quote(project_title)}",
        "in_sync": in_sync,
        "missing": missing,
        "project": project_number,
        "schema_version": 1,
        "stale_claims": stale_claims,
        "stale_epic_claims": stale_epic_claims,
        "stale_priority": stale_priority,
        "stale_status": stale_status,
        "title": project_title,
    }


def _item_index(items: List[Dict[str, Any]], repository: str) -> Dict[int, Dict[str, Any]]:
    """Index the board's cards for this repository's issues by number.

    An organization board also holds other repositories' issues, whose numbers
    collide with ours, so a card for another repository is ignored.
    """
    indexed = {}
    for item in items:
        content = item.get("content")
        if not isinstance(content, dict) or content.get("__typename") != "Issue":
            continue
        owner = content.get("repository")
        if isinstance(owner, dict) and str(owner.get("nameWithOwner", "")).casefold() != repository.casefold():
            continue
        number = _integer(content, "number", "project issue")
        item_id = _text(item, "id", "project item")
        if number in indexed:
            if indexed[number]["id"] == item_id:
                # The same item on two pages: a pagination overlap while cards are
                # being added, not a second card.
                continue
            raise QewError(
                f"indeterminate: duplicate project item for #{number} "
                f"({indexed[number]['id']}, {item_id})",
                4,
            )
        indexed[number] = {"id": item_id, "values": _item_values(item, number)}
    return indexed


def _item_values(item: Dict[str, Any], number: int) -> Dict[str, str]:
    values = {}
    for value in _connection_nodes(item, "fieldValues", number):
        if value.get("__typename") != "ProjectV2ItemFieldSingleSelectValue":
            continue
        field = _mapping(value, "field", "project item field value")
        field_name = _text(field, "name", "project item field")
        values[field_name] = _text(value, "name", "project item field value")
    return values


def _fields(structure: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    connection = _mapping(structure, "fields", "project")
    return [
        field
        for field in _nodes(connection, "project fields")
        if field.get("__typename") == "ProjectV2SingleSelectField" and field.get("name") == name
    ]


def _single_field(structure: Dict[str, Any], name: str) -> Dict[str, Any]:
    matches = _fields(structure, name)
    if len(matches) != 1:
        raise QewError(f"indeterminate: expected one project field {name}", 4)
    return matches[0]


def _new_options(specs: Tuple[OptionSpec, ...]) -> List[Dict[str, str]]:
    return [
        {"name": spec.name, "color": spec.color, "description": spec.description}
        for spec in specs
    ]


def _option_ids(field: Dict[str, Any]) -> Dict[str, str]:
    options = field.get("options")
    if not isinstance(options, list):
        raise QewError("indeterminate: invalid project field options", 4)
    result = {}
    for option in options:
        if not isinstance(option, dict):
            raise QewError("indeterminate: invalid project field option", 4)
        result[_text(option, "name", "project field option")] = _text(
            option, "id", "project field option"
        )
    return result


def _required_option(options: Dict[str, str], name: str, field: str) -> str:
    option = options.get(name)
    if option is None:
        raise QewError(f"indeterminate: missing {field} option {name}", 4)
    return option


def _connection_nodes(value: Dict[str, Any], key: str, number: int) -> List[Dict[str, Any]]:
    connection = _mapping(value, key, f"item #{number}")
    page_info = _mapping(connection, "pageInfo", key)
    if page_info.get("hasNextPage") is not False:
        raise QewError(f"indeterminate: incomplete pagination in {key}", 4)
    return _nodes(connection, key)


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _nodes(connection: Dict[str, Any], context: str) -> List[Dict[str, Any]]:
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid nodes in {context}", 4)
    return nodes


def _text(value: Dict[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _integer(value: Dict[str, Any], key: str, context: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child
