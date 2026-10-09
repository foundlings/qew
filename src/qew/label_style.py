"""Which labels qew owns and how every label looks: priorities read P0..P4, managed labels carry a fixed palette,
and any other label gets a stable colour derived from its name (the same on every machine).

A human's label keeps a colour the team picked: only the neutral default grey is ever repainted.
"""

from dataclasses import dataclass
import re
import zlib

from qew.forge import LABEL_DESCRIPTION, PRIORITY_OPTIONS

# Namespaces sync writes itself. `kind:` and `priority:` are the retired spellings of the kind and priority labels
# (`priority:` is also GitLab's scoped board label): still managed so they are never read back as a human's label, but
# never restyled.
MANAGED_PREFIXES = ("qew:", "kind:", "priority:", "author:", "status:")
KIND_LABELS = ("epic", "task")  # a plain issue carries no kind label
_PRIORITY_LABEL = re.compile(r"^P[0-4]$")

_PRIORITY_HEX = {"RED": "d73a4a", "ORANGE": "fb8500", "YELLOW": "fbca04", "GREEN": "2da44e", "GRAY": "8b949e"}
_KIND_HEX = {"epic": "5319e7", "task": "006b75"}
_PREFIX_HEX = {"qew:": "bfdadc", "status:": "c2e0c6", "author:": "d4c5f9"}
_PALETTE = ("0e8a16", "1d76db", "5319e7", "b60205", "e99695", "f9d0c4", "fef2c0", "c5def5", "bfd4f2", "d4c5f9", "a2eeef", "7057ff")
# The colours a label has when nobody picked one: ours, an empty one, black, and GitLab's default blue.
DEFAULT_COLOURS = frozenset({"", "ededed", "000000", "428bca"})


@dataclass(frozen=True)
class LabelStyle:
    color: str  # six lower-case hex digits, no leading #
    description: str


def is_priority(name: str) -> bool:
    return _PRIORITY_LABEL.match(name) is not None


def is_managed(name: str) -> bool:
    """A label sync owns: a managed namespace, a kind label or one of P0..P4."""
    return name.startswith(MANAGED_PREFIXES) or name in KIND_LABELS or is_priority(name)


def managed_style(name: str) -> LabelStyle | None:
    """The look of a label qew owns, or None for a human's label and for the retired `kind:` and `priority:` spellings."""
    if is_priority(name):
        _label, colour, description = PRIORITY_OPTIONS[int(name[1:])]
        return LabelStyle(_PRIORITY_HEX[colour], description)
    if name in _KIND_HEX:
        return LabelStyle(_KIND_HEX[name], LABEL_DESCRIPTION)
    for prefix, colour in _PREFIX_HEX.items():
        if name.startswith(prefix):
            return LabelStyle(colour, LABEL_DESCRIPTION)
    return None


def hashed_colour(name: str) -> str:
    """A stable colour for any label name: a fixed palette indexed by a checksum of the name."""
    return _PALETTE[zlib.crc32(name.encode("utf-8")) % len(_PALETTE)]


def style_of(name: str) -> LabelStyle:
    """What a new label is created with: qew's own palette entry, else a colour derived from the name."""
    return managed_style(name) or LabelStyle(hashed_colour(name), LABEL_DESCRIPTION)


def repaint(name: str, colour: str, description: str | None) -> LabelStyle | None:
    """The style to give an existing label, or None to leave it. qew's own labels are held to the palette; any other
    label is only painted while it still has the neutral default colour, and then keeps its own description."""
    wanted = managed_style(name)
    if wanted is not None:
        same = colour.lstrip("#").lower() == wanted.color and (description or "") == wanted.description
        return None if same else wanted
    if is_managed(name):  # the retired kind: and priority: spellings are pruned, not painted
        return None
    if colour.lstrip("#").lower() in DEFAULT_COLOURS:
        return LabelStyle(hashed_colour(name), description or "")
    return None
