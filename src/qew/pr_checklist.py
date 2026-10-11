"""The progress checklist in a pull request body: find boxes outside code fences, tick them in place.

Boxes are `- [ ] text <!-- pr-progress:ID -->` lines. The marker is the stable address; a box
without one is addressed by its 1-based position, or `@N` when a marker already uses the number N.
Ticking changes one character per box and keeps every other byte, line endings included.
"""

from dataclasses import dataclass
import re
from collections.abc import Sequence

from qew.errors import QewError

_BOX = re.compile(r"^(?P<lead>[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[)(?P<mark>[ xX])(?P<tail>\](?:[ \t].*)?)$")
_OPEN_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_MARKER = re.compile(r"<!--\s*pr-progress:(?P<id>[A-Za-z0-9_.:-]+)\s*-->")
_ENDINGS = ("\r\n", "\n", "\r")


@dataclass(frozen=True)
class Box:
    line: int  # index into the body's lines
    position: int  # 1-based among the boxes
    marker: str  # "" when the line has none
    address: str  # what `qew pr check` accepts for it
    text: str
    ticked: bool


def _split(line: str) -> tuple[str, str]:
    for ending in _ENDINGS:
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def boxes(body: str) -> list[Box]:
    """Every checklist box outside a fenced code block, in order, with its address."""
    found: list[tuple[int, str, str, bool]] = []
    fence: tuple[str, int] | None = None  # (character, length) of the open fence
    for index, raw in enumerate(body.splitlines(keepends=True)):
        line, _ = _split(raw)
        if fence is not None:
            closing = re.fullmatch(rf" {{0,3}}({re.escape(fence[0])}{{{fence[1]},}})[ \t]*", line)
            if closing:
                fence = None
            continue
        opening = _OPEN_FENCE.match(line)
        if opening and not (opening.group("fence")[0] == "`" and "`" in opening.group("info")):
            fence = (opening.group("fence")[0], len(opening.group("fence")))
            continue
        matched = _BOX.match(line)
        if matched:
            found.append((index, matched.group("tail"), matched.group("mark"), matched.group("mark") != " "))
    markers: dict[int, str] = {}
    for number, (index, tail, _, _) in enumerate(found, start=1):
        marker = _MARKER.search(tail)
        if marker and marker.group("id") not in markers.values():
            markers[number] = marker.group("id")
    taken = set(markers.values())
    result: list[Box] = []
    for number, (index, tail, _, ticked) in enumerate(found, start=1):
        marker_id = markers.get(number, "")
        address = marker_id or (str(number) if str(number) not in taken else f"@{number}")
        text = _MARKER.sub("", tail[1:]).strip()
        result.append(Box(index, number, marker_id, address, text, ticked))
    return result


def resolve(listing: Sequence[Box], names: Sequence[str]) -> list[Box]:
    """The boxes named by `names`; an unknown or repeated name is exit 2 and nothing is chosen."""
    chosen: list[Box] = []
    for name in names:
        match = next((b for b in listing if b.address == name), None)
        if match is None and name.startswith("@") and name[1:].isdigit():
            match = next((b for b in listing if b.position == int(name[1:])), None)
        if match is None:
            raise QewError(f"no checklist item `{name}`; `qew pr check NUMBER --list` shows them", 2)
        if match in chosen:
            raise QewError(f"checklist item `{name}` is named twice", 2)
        chosen.append(match)
    return chosen


def tick(body: str, targets: Sequence[Box]) -> str:
    """`body` with the box on each target line set to `x`; all other bytes are untouched."""
    lines = body.splitlines(keepends=True)
    for box in targets:
        content, ending = _split(lines[box.line])
        matched = _BOX.match(content)
        if matched is None:
            raise QewError("the checklist changed while it was being edited", 3)
        lines[box.line] = matched.group("lead") + "x" + matched.group("tail") + ending
    return "".join(lines)


def drop(body: str, target: Box) -> str:
    """`body` without the line of the target box; all other bytes are untouched."""
    lines = body.splitlines(keepends=True)
    if _BOX.match(_split(lines[target.line])[0]) is None:
        raise QewError("the checklist changed while it was being edited", 3)
    del lines[target.line]
    return "".join(lines)
