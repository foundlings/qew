"""One table of `.qew/config.toml`, read with its keys and their types checked (unknown keys refused)."""

from pathlib import Path
import tomllib
from typing import cast
from collections.abc import Iterable

from qew.jsontypes import JsonObject

from qew.errors import QewError


def read(path: Path, name: str, keys: dict[str, type]) -> JsonObject:
    """The table `name` (empty when absent); a key outside `keys` or of the wrong type is exit 1."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{path}: {error}", 1) from None
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise QewError(f"[{name}] must be a table", 1)
    for key, value in section.items():
        if key not in keys:
            raise QewError(f"[{name}] has an unknown key `{key}`", 1)
        if not isinstance(value, keys[key]) or (keys[key] is list and not all(isinstance(v, str) for v in cast(Iterable[object], value))):
            raise QewError(f"[{name}] {key} has the wrong type", 1)
    return section
