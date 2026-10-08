"""Create a missing team (area) label without overwriting an existing one."""

import re
from typing import Dict, Optional

from qew.errors import QewError
from qew.github import fetch_repository_labels, upsert_label
from qew.model import reject_reserved_areas


DEFAULT_COLOR = "EDEDED"
DEFAULT_DESCRIPTION = "Team label"
_NAME_LIMIT = 50
_DESCRIPTION_LIMIT = 100
_COLOR = re.compile(r"[0-9A-Fa-f]{6}")


def validate(name: str, color: str, description: str) -> None:
    if not name or name != name.strip() or len(name) > _NAME_LIMIT:
        raise QewError(f"area name must be 1-{_NAME_LIMIT} characters without edge spaces", 2)
    if "," in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise QewError("area name must not contain commas or control characters", 2)
    reject_reserved_areas([name])
    if not _COLOR.fullmatch(color):
        raise QewError("--color must be six hex digits without a leading #", 2)
    if len(description) > _DESCRIPTION_LIMIT or any(
        ord(character) < 32 or ord(character) == 127 for character in description
    ):
        raise QewError(f"--description must be one line of at most {_DESCRIPTION_LIMIT} characters", 2)


def ensure_area(
    repository: str,
    name: str,
    color: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    """Create the label when missing; True when created. Never overwrites."""
    color = DEFAULT_COLOR if color is None else color
    description = DEFAULT_DESCRIPTION if description is None else description
    validate(name, color, description)
    if name.casefold() in _names(repository):
        return False
    upsert_label(repository, name=name, color=color.upper(), description=description, force=False)
    if name.casefold() not in _names(repository):
        raise QewError(f"indeterminate: area label did not persist: {name}", 4)
    return True


def _names(repository: str) -> Dict[str, bool]:
    names: Dict[str, bool] = {}
    for label in fetch_repository_labels(repository):
        value = label.get("name")
        if not isinstance(value, str):
            raise QewError("indeterminate: invalid repository label", 4)
        names[value.casefold()] = True
    return names
