"""Item fields: named values beside the labels, stored in the item header as one JSON line.

A field is `name -> values` (one or more, order kept). Names are lower-case words; a value is one printable line.
`phase: 12`, `wp: WP-7` and `plan: a, b` are what labels like `phase:12` used to spell.
"""

import json
import re
from collections.abc import Iterable, Mapping

from qew.errors import QewError

NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
MAX_VALUE = 200
MAX_VALUES = 50

type Fields = tuple[tuple[str, tuple[str, ...]], ...]  # sorted by name; no name has zero values


def _values(name: str, raw: object, where: str) -> tuple[str, ...]:
    found = [raw] if isinstance(raw, str) else raw
    if not isinstance(found, (list, tuple)) or not found or len(found) > MAX_VALUES:
        raise QewError(f"{where}: field `{name}` takes a value or a list of 1 to {MAX_VALUES} values", 2)
    values: list[str] = []
    for value in found:
        if not isinstance(value, str) or not value.strip() or not value.isprintable() or len(value) > MAX_VALUE:
            raise QewError(f"{where}: a value of field `{name}` is one printable line of at most {MAX_VALUE} characters", 2)
        if value.strip() not in values:
            values.append(value.strip())
    return tuple(values)


def normalise(raw: Mapping[str, object], where: str = "fields") -> Fields:
    """Validated fields from `{name: value-or-values}`; an empty list means no values and drops the name."""
    kept: dict[str, tuple[str, ...]] = {}
    for name, value in raw.items():
        if not NAME.fullmatch(name):
            raise QewError(f"{where}: `{name}` is not a field name (lower-case letters, digits, `-` and `_`, starting with a letter)", 2)
        if isinstance(value, (list, tuple)) and not value:
            continue
        kept[name] = _values(name, value, where)
    return tuple(sorted(kept.items()))


def encode(fields: Fields) -> str:
    """The header text: compact JSON, a bare string for one value, sorted names. Empty fields are the empty string."""
    if not fields:
        return ""
    return json.dumps({name: values[0] if len(values) == 1 else list(values) for name, values in fields},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def decode(text: str, where: str = "item") -> Fields:
    if not text:
        return ()
    try:
        raw = json.loads(text)
    except ValueError:
        raise QewError(f"{where}: the fields header is not JSON", 1) from None
    if not isinstance(raw, dict):
        raise QewError(f"{where}: the fields header is a JSON object", 1)
    try:
        return normalise(raw, f"{where}: fields")
    except QewError as error:
        raise QewError(str(error), 1) from None


def as_dict(fields: Fields) -> dict[str, list[str]]:
    return {name: list(values) for name, values in fields}


def parse_pairs(pairs: Iterable[str], where: str = "--field") -> dict[str, list[str]]:
    """`NAME=VALUE` arguments; a repeated name collects its values in order."""
    found: dict[str, list[str]] = {}
    for pair in pairs:
        name, equals, value = pair.partition("=")
        if not equals:
            raise QewError(f"{where} {pair!r}: expected NAME=VALUE", 2)
        found.setdefault(name, []).append(value)
    normalise(found, where)  # refuse before anything is written
    return found


def changed(fields: Fields, put: Mapping[str, object], clear: Iterable[str] = ()) -> Fields:
    """`fields` with the named fields replaced by `put` (an empty list removes one) and `clear` names removed."""
    current = dict(fields)
    for name in (*clear, *(n for n, v in put.items() if isinstance(v, (list, tuple)) and not v)):
        current.pop(name, None)
    current.update(normalise(put))
    return tuple(sorted(current.items()))


def matches(fields: Fields, wanted: str) -> bool:
    """True when the item holds `NAME` (any value) or `NAME=VALUE`; ASCII case does not matter."""
    name, equals, value = wanted.partition("=")
    for have, values in fields:
        if have == name.lower():
            return not equals or value.lower() in (v.lower() for v in values)
    return False
