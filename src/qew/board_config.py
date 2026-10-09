"""Named board files: `views/NAME.json` in the ledger, one file per board so branches merge cleanly."""

from dataclasses import dataclass, replace
import json
import re
from typing import cast

from qew import board
from qew.errors import QewError
from qew.forge import COLUMNS
from qew.jsontypes import JsonObject
from qew.ledger_store import Ledger

DEFAULT_NAME = "default"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_KEYS = ("name", "columns", "sort", "group_by", "filter")


@dataclass(frozen=True)
class View:
    name: str
    columns: tuple[str, ...] = COLUMNS
    sort: str = "priority"
    group_by: str = "column"
    filter: str = ""

    def as_json(self) -> JsonObject:
        return {"name": self.name, "columns": list(self.columns), "sort": self.sort, "group_by": self.group_by, "filter": self.filter}


def valid_name(name: str) -> None:
    if not _NAME.match(name):
        raise QewError(f"board name `{name}` must be lowercase letters, digits and dashes (at most 40)", 2)


def validate(value: object, stem: str) -> tuple[View | None, list[str]]:
    """The view a file describes, or the problems that make it unusable."""
    if not isinstance(value, dict):
        return None, ["must be a JSON object"]
    problems = [f"unknown key `{key}`" for key in value if key not in _KEYS]
    name, columns = value.get("name"), value.get("columns")
    if name != stem:
        problems.append(f"name must equal the file name `{stem}`")
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns) or not columns:
        problems.append("columns must be a non-empty list of status names")
    else:
        problems.extend(f"unknown column `{c}`" for c in columns if c not in COLUMNS)
        problems.extend(f"duplicate column `{c}`" for c in sorted({c for c in columns if columns.count(c) > 1}))
    sort, group, text = value.get("sort", "priority"), value.get("group_by", "column"), value.get("filter", "")
    if not isinstance(sort, str) or sort.partition(":")[0] not in board.SORT_KEYS or sort.partition(":")[2] not in ("", "asc", "desc"):
        problems.append(f"sort must be one of {', '.join(board.SORT_KEYS)} optionally followed by :desc")
    if group not in board.GROUP_KEYS:
        problems.append(f"group_by must be one of {', '.join(board.GROUP_KEYS)}")
    if not isinstance(text, str):
        problems.append("filter must be a string")
    else:
        try:
            board.filter_terms(text)
        except QewError as error:
            problems.append(str(error))
    if problems:
        return None, problems
    return View(stem, tuple(cast(list[str], columns)), sort, group, text), []


def _read(ledger: Ledger, stem: str) -> tuple[View | None, list[str]]:
    path = ledger.views_dir / f"{stem}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        return None, [str(error)]
    return validate(value, stem)


def load(ledger: Ledger, name: str) -> View | None:
    """The named view, None when no such file; a file that fails validation is an error, never a default."""
    if not (ledger.views_dir / f"{name}.json").is_file():
        return None
    view, problems = _read(ledger, name)
    if view is None:
        raise QewError(f"views/{name}.json: {'; '.join(problems)}", 1)
    return view


def names(ledger: Ledger) -> list[str]:
    return sorted(p.stem for p in ledger.views_dir.glob("*.json")) if ledger.views_dir.is_dir() else []


def problems(ledger: Ledger) -> list[str]:
    found: list[str] = []
    for stem in names(ledger):
        _view, issues = _read(ledger, stem)
        found.extend(f"views/{stem}.json: {issue}" for issue in issues)
    return found


def create(ledger: Ledger, view: View) -> None:
    valid_name(view.name)
    _check(view)
    if (ledger.views_dir / f"{view.name}.json").exists():
        raise QewError(f"board `{view.name}` already exists; use `qew board set`", 2)
    ledger.write_view(view.name, view.as_json(), overwrite=False)


def update(ledger: Ledger, name: str, changes: JsonObject) -> None:
    current = load(ledger, name)
    if current is None:
        raise QewError(f"no board named `{name}`; create it with `qew board create`", 2)
    view = replace(current, **changes)
    _check(view)
    ledger.write_view(name, view.as_json(), overwrite=True)


def _check(view: View) -> None:
    _checked, issues = validate(view.as_json(), view.name)
    if issues:
        raise QewError(f"board `{view.name}`: {'; '.join(issues)}", 2)
