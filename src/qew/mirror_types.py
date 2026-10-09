"""The fixed shapes of a mirror record and of the forge baseline it carries."""

from typing import TypedDict, Unpack, cast


class Seen(TypedDict):
    """What the forge showed at the last sync or pull: the baseline a later pull diffs against."""

    title: str
    body: str
    labels: list[str]
    open: bool
    assignee: str
    comments: list[str]


class MirrorState(TypedDict, total=False):
    """The keys of a mirror record that may be absent: committed identity extras and this machine's sync state."""

    node_id: str
    issue_url: str
    head: str
    opener: str
    items: list[str]
    synced_at: str
    synced_event: str
    synced_hash: str
    baselined: bool
    linked: list[str]  # item ids whose dependency link is on the forge
    seen: Seen
    column: str | None
    priority: str | None
    draft: bool
    state: str
    pr_url: str
    milestone_closed: bool
    _target: str


class _MirrorIdentity(TypedDict):
    forge: str
    url: str
    kind: str
    number: int


class MirrorRecord(MirrorState, _MirrorIdentity):
    """One item's link to one forge target."""


def updated(record: MirrorRecord, **changes: Unpack[MirrorState]) -> MirrorRecord:
    """The record without the in-memory `_target` marker, with `changes` applied; the input is not modified."""
    kept = {key: value for key, value in record.items() if key != "_target"}
    return cast(MirrorRecord, {**kept, **changes})
