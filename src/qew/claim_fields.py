"""The optional lane fields of a claim: shape rules shared by the command and the event schema."""

import re
from typing import Optional

from qew.errors import QewError

KINDS = ("compute", "token")
_LANE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def lane(value: str, exit_code: int = 2) -> str:
    if not _LANE.fullmatch(value) or "--" in value:
        raise QewError(
            f"lane `{value}` must be 1 to 64 of A-Za-z0-9._-, start with a letter or digit and not contain `--`", exit_code
        )
    return value


def workspace(value: str, exit_code: int = 2) -> str:
    if not value.startswith("/") or not value.isprintable() or "--" in value:
        raise QewError(f"workspace `{value}` must be an absolute printable path without `--`", exit_code)
    return value


def kind(value: str, exit_code: int = 2) -> str:
    if value not in KINDS:
        raise QewError(f"claim kind must be one of {', '.join(KINDS)}, not `{value}`", exit_code)
    return value


def validate(lane_value: Optional[str], workspace_value: Optional[str], kind_value: Optional[str], exit_code: int = 2) -> None:
    """Check whichever fields are present; workspace and kind are meaningless without a lane."""
    if lane_value is not None:
        lane(lane_value, exit_code)
    elif workspace_value is not None or kind_value is not None:
        raise QewError("--workspace and --kind need --lane", exit_code)
    if workspace_value is not None:
        workspace(workspace_value, exit_code)
    if kind_value is not None:
        kind(kind_value, exit_code)
