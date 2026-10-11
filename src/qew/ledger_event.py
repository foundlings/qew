"""Append-only events and the state machine folded from them.

Current state is the fold of an item's events in ID order. A transition the
machine does not allow is a typed refusal, never a silent no-op.
"""

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any

from qew import __version__, attachments, claim_fields, instants
from qew.errors import QewError
from qew.ledger_item import CHANGE_ID, absence_problem

CLOSE_REASONS = ("not_planned", "duplicate", "superseded")


def valid_actor(text: str) -> bool:
    """An actor is one printable token: no whitespace, commas or control characters (it may become a forge label)."""
    return bool(text) and text.isprintable() and " " not in text and "," not in text  # the only printable whitespace is U+0020


class State(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CLOSED = "closed"


# type -> (required string fields, allowed source states; None = initial event only)
_RULES: dict[str, tuple[tuple[str, ...], tuple[State, ...] | None]] = {
    "drafted": ((), None),
    "approved": ((), (State.DRAFT,)),  # needs `quote` (a human) or `filed_by` (an agent filing into the queue)
    "reverted": (("reason", "quote"), (State.APPROVED,)),
    "claimed": (("owner",), (State.APPROVED,)),
    "released": ((), (State.CLAIMED,)),
    "evidence": (("vcs", "repo"), (State.APPROVED, State.CLAIMED, State.COMPLETED)),  # after completion: recovered proof for a historical completion
    "completed": ((), (State.CLAIMED,)),  # also from approved when delivered elsewhere or by its children
    "reopened": ((), (State.COMPLETED, State.CLOSED)),
    "closed": (("reason",), (State.DRAFT, State.APPROVED, State.CLAIMED, State.COMPLETED)),
    "field": (("key",), (State.DRAFT, State.APPROVED, State.CLAIMED, State.COMPLETED, State.CLOSED)),
    # Forge replication (docs/specs/ledger.md, Replication): never change state by themselves.
    "commented": (("text",), tuple(State)),
    # Comments as records (docs/specs/ledger.md, Comments): edits and deletes name a comment by its event id.
    "comment_edited": (("ref", "text"), tuple(State)),
    "comment_deleted": (("ref",), tuple(State)),
    # Extra assignees beside the claim owner (docs/specs/ledger.md, Assignees): forge logins.
    "assigned": (("login",), tuple(State)),
    "unassigned": (("login",), tuple(State)),
    # What the forge's own timeline recorded before the issue reached the ledger (docs/specs/ledger.md, Forge timeline): history only.
    "forge_timeline": (("kind", "who", "when"), tuple(State)),
    # An earlier wording of the issue body, kept at import (docs/specs/ledger.md, Forge timeline): history only.
    "body_revision": (("who", "when", "hash", "text"), tuple(State)),
    # The item's author is the human, not the tool that filed it (docs/specs/ledger.md, Authorship): the newest one wins.
    "author_corrected": (("author",), tuple(State)),
    "forge_divergence": (("target", "field"), tuple(State)),
    "divergence_accepted": (("ref",), tuple(State)),
    "divergence_rejected": (("ref",), tuple(State)),
    # A ledger merge conflict settled by taking one side (qew resolve ledger): the other side is kept in `discarded`.
    "conflict_resolved": (("took", "reason"), tuple(State)),
    # Claim arbitration (docs/specs/ledger.md, Authority and topology).
    "claim_confirmed": (("ref",), tuple(State)),
    "claim_lost": (("ref",), tuple(State)),
    "edge": (("op", "need"), tuple(State)),
    # Item attachments (docs/specs/ledger.md, Attachments): names mapped to immutable content-addressed files.
    "attached": (("path", "hash", "blob"), tuple(State)),
    "detached": (("path",), tuple(State)),
}
# A `completed` event with one of these keys finished the item without a claim: its children
# finished it, the code that delivers it landed without anyone claiming it, or it was made under a
# provisional (offline) claim that sync may later record as lost: the completion stands either way.
_UNCLAIMED_COMPLETION = ("children_finished", "delivered_by", "provisional_claim", "bulk_quote")  # bulk_quote: `qew complete --manifest`
MAX_COMMENT_BYTES = 20_000
PROVENANCE = ("import", "forge", "forge-import")
_INITIAL = {"drafted": State.DRAFT, "approved": State.APPROVED}
_NEXT = {
    "approved": State.APPROVED,
    "reverted": State.DRAFT,
    "claimed": State.CLAIMED,
    "released": State.APPROVED,
    "completed": State.COMPLETED,
    "reopened": State.APPROVED,
    "closed": State.CLOSED,
}
TYPES = tuple(_RULES)


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    at: str
    actor: str
    data: dict[str, Any]

    def get(self, key: str, default: Any = "") -> Any:
        return self.data.get(key, default)

    def to_json(self) -> str:
        return json.dumps(
            {"type": self.type, "at": self.at, "actor": self.actor, **self.data},
            sort_keys=True,
            ensure_ascii=False,
        ) + "\n"


def parse_event_trusted(event_id: str, text: str) -> Event:
    """An event from text that `parse_event` already accepted (the snapshot cache validated it when it entered): no checks again."""
    value = json.loads(text)
    return Event(event_id, value["type"], value["at"], value["actor"], {key: item for key, item in value.items() if key not in ("type", "at", "actor")})


def parse_event(event_id: str, text: str, where: str = "event") -> Event:
    try:
        value = json.loads(text)
    except ValueError as error:
        raise QewError(f"{where}: not valid JSON ({error})", 1) from None
    if not isinstance(value, dict):
        raise QewError(f"{where}: an event is one JSON object", 1)
    kind = value.get("type")
    if kind not in _RULES:
        raise QewError(f"{where}: unknown event type `{kind}` (written by a newer qew? this is qew {__version__}; upgrade it)", 1)
    for key in ("at", "actor"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise QewError(f"{where}: event is missing `{key}`", 1)
    if instants.parse_instant(value["at"]) is None:
        raise QewError(f"{where}: event time `{value['at']}` is not a UTC instant (YYYY-MM-DDTHH:MM:SSZ)", 1)
    for key in _RULES[kind][0]:
        if key not in value or value[key] in ("", None) or (isinstance(value[key], str) and not value[key].strip()):
            raise QewError(f"{where}: `{kind}` event is missing `{key}`", 1)
    if kind == "approved" and not any(isinstance(value.get(key), str) and value[key].strip() for key in ("quote", "filed_by")):
        raise QewError(f"{where}: `approved` event is missing `quote` or `filed_by`", 1)
    if kind == "completed" and ("evidence" in value or "absent" in value):
        cited = value.get("evidence")
        if "evidence" in value and (not isinstance(cited, list) or not cited or not all(isinstance(c, str) and CHANGE_ID.fullmatch(c) for c in cited)):
            raise QewError(f"{where}: `evidence` is a non-empty list of change ids", 1)
        if "absent" in value:
            problem = absence_problem(f"{value['absent']}: {value.get('absent_reason', '')}") if isinstance(value["absent"], str) else "absent is text"
            if problem:
                raise QewError(f"{where}: {problem}", 1)
            if "evidence" in value:
                raise QewError(f"{where}: `evidence` and `absent` are exclusive", 1)
    if kind == "completed" and "bulk_quote" in value and (not isinstance(value["bulk_quote"], str) or not value["bulk_quote"].strip()):
        raise QewError(f"{where}: `bulk_quote` must be a human's words", 1)
    if kind == "closed" and value["reason"] not in CLOSE_REASONS:
        raise QewError(f"{where}: close reason must be one of {', '.join(CLOSE_REASONS)}", 1)
    if "provenance" in value:
        allowed = ("import", "forge-import") if kind == "completed" else PROVENANCE
        if value["provenance"] not in allowed:
            raise QewError(f"{where}: `provenance` {value['provenance']!r} is not valid on a `{kind}` event", 1)
        ids = value.get("original_ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise QewError(f"{where}: original_ids must be a list of strings", 1)
        if kind == "completed" and value["provenance"] == "import" and not ids:
            raise QewError(f"{where}: an import completion must list the original_ids it stands for", 1)
    if kind == "evidence":
        ids = value.get("change_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise QewError(f"{where}: evidence needs a non-empty change_ids list", 1)
        if value["vcs"] not in ("jj", "git"):
            raise QewError(f"{where}: evidence vcs must be jj or git", 1)
    if "via" in value and (not isinstance(value["via"], str) or not valid_actor(value["via"])):
        raise QewError(f"{where}: `via` (the tool that wrote the event) must be one word without spaces or commas", 1)
    _check_optional_fields(kind, value, where)
    data = {key: item for key, item in value.items() if key not in ("type", "at", "actor")}
    return Event(event_id, kind, value["at"], value["actor"], data)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(i, str) and i for i in value)


def _check_optional_fields(kind: str, value: dict[str, Any], where: str) -> None:
    """Schema of the optional fields an event may carry; a malformed one makes `qew check` fail loudly."""

    def text(key: str) -> None:
        if key in value and (not isinstance(value[key], str) or not value[key].strip()):
            raise QewError(f"{where}: `{key}` must be non-empty text", 1)

    if kind == "claimed":
        if "provisional" in value and value["provisional"] is not True:
            raise QewError(f"{where}: claim `provisional` must be true when present", 1)
        for key in ("lane", "workspace", "kind"):
            if key in value and not isinstance(value[key], str):
                raise QewError(f"{where}: claim `{key}` must be text", 1)
        try:
            claim_fields.validate(value.get("lane"), value.get("workspace"), value.get("kind"), exit_code=1)
        except QewError as error:
            raise QewError(f"{where}: {error}", 1) from None
    elif kind == "released":
        text("reason")
        text("previous_owner")
        if "forced" in value and not isinstance(value["forced"], bool):
            raise QewError(f"{where}: `forced` must be true or false", 1)
    elif kind == "completed":
        for key in ("reason", "branch", "no_commit", "taken_over_from", "provisional_claim"):
            text(key)
        for key in ("commits", "delivered_by"):
            if key in value and not _strings(value[key]):
                raise QewError(f"{where}: `{key}` must be a non-empty list of commit ids", 1)
        if "prs" in value and not (isinstance(value["prs"], list) and all(type(n) is int and n > 0 for n in value["prs"])):
            raise QewError(f"{where}: `prs` must be a list of pull request numbers", 1)
        if "children_finished" in value and (type(value["children_finished"]) is not int or value["children_finished"] < 1):
            raise QewError(f"{where}: `children_finished` must be a positive count", 1)
    elif kind == "closed":
        for key in ("duplicate_of", "superseded_by", "note"):
            text(key)
        if "duplicate_of" in value and "superseded_by" in value:
            raise QewError(f"{where}: a close names one linked item, `duplicate_of` or `superseded_by`, not both", 1)
    elif kind == "reopened":
        text("reason")
    elif kind == "edge":
        if value["op"] not in ("add", "remove"):
            raise QewError(f"{where}: edge op must be add or remove", 1)
    elif kind == "author_corrected":
        if not isinstance(value["author"], str) or not valid_actor(value["author"]):
            raise QewError(f"{where}: `author` must be one word without spaces or commas", 1)
    elif kind in ("assigned", "unassigned"):
        if not isinstance(value["login"], str) or not valid_actor(value["login"]):
            raise QewError(f"{where}: `login` must be one word without spaces or commas", 1)
    elif kind == "forge_timeline":
        for key in ("kind", "who", "when"):
            if not isinstance(value[key], str) or not value[key].strip():
                raise QewError(f"{where}: `{key}` must be non-empty text", 1)
        if "detail" in value and not isinstance(value["detail"], str):
            raise QewError(f"{where}: `detail` must be text", 1)
    elif kind == "body_revision":
        for key in ("who", "when", "hash", "text"):
            if not isinstance(value[key], str):
                raise QewError(f"{where}: `{key}` must be text", 1)
    elif kind in ("commented", "comment_edited", "comment_deleted"):
        for key in ("reply_to", "ref", "key"):
            if key in value and (not isinstance(value[key], str) or not value[key].strip()):
                raise QewError(f"{where}: `{key}` must be non-empty text", 1)
        if "forced" in value and not isinstance(value["forced"], bool):
            raise QewError(f"{where}: `forced` must be true or false", 1)
    elif kind in ("attached", "detached"):
        bad = attachments.path_error(value["path"]) if isinstance(value["path"], str) else "the path must be text"
        if kind == "attached":
            bad = bad or attachments.digest_error(value["hash"])
            bad = bad or (attachments.blob_error(value["hash"], value["blob"]) if isinstance(value["blob"], str) else "the blob must be text")
            if not bad and (type(value.get("size")) is not int or value["size"] < 0):
                bad = "`size` must be a whole number of bytes"
        if bad:
            raise QewError(f"{where}: `{kind}` event: {bad}", 1)


@dataclass(frozen=True)
class Folded:
    state: State | None
    owner: str
    evidence: tuple[Event, ...]
    errors: tuple[str, ...]
    imported: bool = False  # completed by an import path, without verifiable evidence
    divergences: tuple[Event, ...] = ()  # unresolved: forge-side state changes, and `claimed` events contesting a held item
    claim: dict[str, str | None] | None = None  # {by, kind, lane, workspace} while claimed
    lost_claims: tuple[tuple[str, str], ...] = ()  # (event id, actor) of claims that lost a merged race
    completion: Event | None = None  # the `completed` event that finished the item
    closure: Event | None = None  # the `closed` event while the item is closed


def fold(events: list[Event]) -> Folded:
    """Apply events in ID order; collect every illegal transition instead of stopping.

    Two writers on different branches may both claim an item. A claim the authority recorded as lost
    (`claim_lost`) never held the item. Of two claims that nobody arbitrated, the oldest in the current
    claim period holds it; a later claim by another actor is a recorded lost race, not corruption.
    """
    state: State | None = None
    owner = ""
    claim: dict[str, str | None] | None = None
    lost_claims: list[tuple[str, str]] = []
    completion: Event | None = None
    closure: Event | None = None
    evidence: list[Event] = []
    errors: list[str] = []
    imported = False
    contested: list[Event] = []
    lost = {e.get("ref") for e in events if e.type == "claim_lost"}
    for event in sorted(events, key=lambda item: item.id):
        if event.type == "claimed" and event.id in lost:
            continue  # this claim lost arbitration: it never owned the item
        if event.type == "claimed" and state == State.CLAIMED and event.get("owner") != owner:
            contested.append(event)  # a second owner: a contradiction for accept/reject, not an illegal transition
            lost_claims.append((event.id, event.get("owner")))  # and `qew check` names the loser
            continue
        _, sources = _RULES[event.type]
        if event.type == "completed" and event.get("provenance") in ("import", "forge-import"):
            # Imported history was never claimed here; it may complete from any open state.
            sources = (State.DRAFT, State.APPROVED, State.CLAIMED)
            imported = True
        elif event.type == "completed" and any(key in event.data for key in _UNCLAIMED_COMPLETION):
            sources = (State.APPROVED, State.CLAIMED)
        if sources is None:
            if state is not None:
                errors.append(f"{event.id}: `drafted` is only valid as the first event")
            else:
                state = _INITIAL[event.type]
            continue
        if state is None:
            if event.type == "approved":
                state = State.APPROVED
            else:
                errors.append(f"{event.id}: `{event.type}` before the item was drafted or approved")
            continue
        if event.type == "claimed" and state == State.CLAIMED:
            continue  # the same actor claiming again is one claim
        if state not in sources:
            errors.append(f"{event.id}: `{event.type}` is illegal from state {state.value}")
            continue
        if event.type == "claimed":
            owner = event.get("owner")
            claim = {
                "by": owner,
                "kind": event.get("kind") or "compute",
                "lane": event.get("lane") or None,
                "workspace": event.get("workspace") or None,
            }
        elif event.type in ("released", "reverted", "reopened", "closed"):
            owner = ""
            claim = None
            closure = event if event.type == "closed" else None
            if event.type in ("reopened", "closed", "reverted"):
                completion = None
        elif event.type == "evidence":
            evidence.append(event)
        elif event.type == "completed":
            completion = event
        if event.type in _NEXT:
            state = _NEXT[event.type]
    resolved = {e.get("ref") for e in events if e.type in ("divergence_accepted", "divergence_rejected")}
    open_divergences = tuple(e for e in sorted(events, key=lambda item: item.id)
                             if (e.type == "forge_divergence" and e.id not in resolved) or e in contested)
    return Folded(state, owner, tuple(evidence), tuple(errors), imported, open_divergences, claim,
                  tuple(lost_claims), completion, closure)


def needs_after(base: tuple[str, ...], events: list[Event]) -> tuple[str, ...]:
    """The effective needs: the header's list with every `edge` event applied in ID order.

    Each added or removed edge is its own event file, so two branches that change different edges merge
    without conflict and without losing either one.
    """
    needs = list(base)
    for event in sorted((e for e in events if e.type == "edge"), key=lambda item: item.id):
        need = event.get("need")
        if event.get("op") == "add" and need not in needs:
            needs.append(need)
        elif event.get("op") == "remove" and need in needs:
            needs.remove(need)
    return tuple(needs)


def attachments_after(events: list[Event]) -> dict[str, attachments.Attachment]:
    """The named tree: every `attached` and `detached` event applied in ID order, the last one for a name winning.

    A name that would be both a file and a directory yields to the later event, so the result can always be exported.
    """
    found: dict[str, attachments.Attachment] = {}
    for event in sorted((e for e in events if e.type in ("attached", "detached")), key=lambda item: item.id):
        path = event.get("path")
        if event.type == "detached":
            found.pop(path, None)
            continue
        for other in attachments.clashes(set(found), path):
            del found[other]
        found[path] = attachments.Attachment(path, event.get("hash"), event.get("size"), event.get("blob"), event.id)
    return found


def transition_error(state: State | None, event_type: str) -> str | None:
    """Why `event_type` is refused from `state`, or None when allowed."""
    _, sources = _RULES[event_type]
    if sources is None or state is None:
        return None if event_type in _INITIAL and state is None else f"`{event_type}` has no item state to act on"
    if state in sources:
        return None
    allowed = ", ".join(source.value for source in sources)
    return f"cannot {event_type} an item that is {state.value} (needs: {allowed})"
