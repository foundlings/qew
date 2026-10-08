"""Lane-named claim records carried in issue comments.

A claim on GitHub is an assignment plus the `in-progress` label. Several agent
lanes may share one login, so the assignment cannot say which lane holds the
work. `qew start --lane` therefore writes a structured comment marker naming the
lane, and the current lane is derived from those markers.

Comments are untrusted: anyone who can comment can write a marker. Only markers
written by a current assignee count, and only their validated fields are used.

The current claim is the OLDEST valid claim marker written by a current assignee
after the latest valid release marker written by a current assignee. Comment
order is decided by GitHub, so two lanes racing to claim the same issue agree on
one winner whatever the timing of their verification reads. A marker that does
not validate is skipped with a warning; it never decides the claim and never
blocks a read.
"""

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from qew.errors import QewError


_LANE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_MARKER = re.compile(r"<!-- qew:(claim|release) (\{[^\n]*?\}) -->")
KINDS = ("compute", "token")
DEFAULT_KIND = "compute"


@dataclass(frozen=True)
class Claim:
    lane: str
    workspace: Optional[str]
    by: str
    kind: str = DEFAULT_KIND

    def as_json(self) -> Dict[str, Any]:
        return {"by": self.by, "kind": self.kind, "lane": self.lane, "workspace": self.workspace}


@dataclass(frozen=True)
class Comment:
    author: Optional[str]
    body: str


def is_marker_comment(body: str) -> bool:
    """True when a comment carries one of Qew's own claim or release markers."""
    return _MARKER.search(body) is not None


def validate_lane(lane: str) -> str:
    if _LANE.fullmatch(lane) is None or "--" in lane:
        raise QewError(
            "--lane must be 1-64 letters, digits, '.', '_' or '-', start with a letter "
            "or digit, and must not contain '--'",
            2,
        )
    return lane


def validate_workspace(workspace: str) -> str:
    if (
        not workspace.startswith("/")
        or "--" in workspace
        or any(not character.isprintable() for character in workspace)
    ):
        raise QewError(
            "--workspace must be an absolute, printable path without '--'",
            2,
        )
    return workspace


def validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise QewError("--kind must be one of: " + ", ".join(KINDS), 2)
    return kind


def claim_comment(lane: str, workspace: Optional[str], kind: str = DEFAULT_KIND) -> str:
    where = "" if workspace is None else f" (workspace `{workspace}`)"
    marker = _marker("claim", {"lane": lane, "workspace": workspace, "kind": kind})
    return f"Claimed by lane `{lane}`{where}.\n\n{marker}"


def release_comment(
    lane: Optional[str], by: str, reason: str, forced_by_lane: Optional[str] = None
) -> str:
    held = "a claim with no lane" if lane is None else f"the claim of lane `{lane}`"
    forced = "" if forced_by_lane is None else f" (forced by lane `{forced_by_lane}`)"
    marker = _marker("release", {"lane": lane, "by": by, "reason": reason})
    return f"{by} released {held}{forced}.\n\nReason: {reason}\n\n{marker}"


def current_claim(
    comments: Sequence[Comment],
    assignees: Sequence[str],
    warn: Optional[Callable[[str], None]] = None,
) -> Optional[Claim]:
    """The oldest valid claim by a current assignee since their latest valid release."""
    trusted = set(assignees)
    claim: Optional[Claim] = None
    for comment in comments:
        if comment.author is None or comment.author not in trusted:
            continue
        for kind, payload in _MARKER.findall(comment.body):
            if kind == "release":
                if _valid_release(payload):
                    claim = None
                elif warn is not None:
                    warn(f"ignoring malformed release marker by {comment.author}")
                continue
            parsed = _parse_claim(payload, comment.author)
            if parsed is None:
                if warn is not None:
                    warn(f"ignoring malformed claim marker by {comment.author}")
                continue
            if claim is None:
                claim = parsed
    return claim


def _parse_claim(payload: str, author: str) -> Optional[Claim]:
    value = _decode(payload)
    if value is None or not {"lane", "workspace"} <= set(value) <= {"lane", "workspace", "kind"}:
        return None
    lane = value["lane"]
    workspace = value["workspace"]
    kind = value.get("kind", DEFAULT_KIND)
    if not isinstance(lane, str) or (workspace is not None and not isinstance(workspace, str)):
        return None
    try:
        validate_lane(lane)
        if workspace is not None:
            validate_workspace(workspace)
        validate_kind(kind)
    except QewError:
        return None
    return Claim(lane=lane, workspace=workspace, by=author, kind=kind)


def _valid_release(payload: str) -> bool:
    value = _decode(payload)
    return value is not None and set(value) == {"lane", "by", "reason"}


def decode_comments(pages: List[Dict[str, Any]], issue: int) -> List[Comment]:
    """Oldest-first comments from newest-first backward pages."""
    ordered: List[Comment] = []
    for page in pages:
        connection = (
            page.get("data", {}).get("repository", {}).get("issue", {}).get("comments")
            if isinstance(page, dict)
            else None
        )
        if not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list):
            raise QewError(f"indeterminate: invalid comments for #{issue}", 4)
        batch = []
        for node in connection["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("body"), str):
                raise QewError(f"indeterminate: invalid comment on #{issue}", 4)
            author = node.get("author")
            login = None
            if author is not None:
                if not isinstance(author, dict) or not isinstance(author.get("login"), str):
                    raise QewError(f"indeterminate: invalid comment author on #{issue}", 4)
                login = author["login"]
            batch.append(Comment(author=login, body=node["body"]))
        ordered = batch + ordered
    return ordered


def _marker(kind: str, value: Dict[str, Any]) -> str:
    # Escape angle brackets so free text can never close the HTML comment.
    encoded = json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
    return f"<!-- qew:{kind} {encoded} -->"


def _decode(payload: str) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
