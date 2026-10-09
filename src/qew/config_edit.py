"""Minimal, comment-preserving edits of `.qew/config.toml`, by line, with the standard library only.

Only whole `[[mirror]]` blocks and single `key = value` lines in a table are touched; every other byte,
comment and blank line of the file stays as written. The result is parsed again before it is returned.
"""

from pathlib import Path
import re
import tomllib
from collections.abc import Callable

from qew.errors import QewError

_HEADER = re.compile(r"^\s*(\[\[?)\s*([A-Za-z0-9_.\"-]+)\s*\]\]?\s*(#.*)?$")


def _headers(lines: list[str]) -> list[tuple[int, str, bool]]:
    """(line index, table name, is array-of-tables) for every table header outside multi-line strings."""
    found = []
    quoted: str | None = None
    for index, line in enumerate(lines):
        if quoted is not None:
            if quoted in line:
                quoted = None
            continue
        for fence in ('"""', "'''"):
            if line.count(fence) % 2 == 1:
                quoted = fence
        match = _HEADER.match(line)
        if quoted is None and match:
            found.append((index, match.group(2), match.group(1) == "[["))
    return found


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _block(lines: list[str], headers: list[tuple[int, str, bool]], position: int) -> tuple[int, int]:
    """The line range [start, end) of one table: its attached comments, header and key lines."""
    header_line = headers[position][0]
    start = header_line
    while start > 0 and _is_comment(lines[start - 1]):
        start -= 1
    limit = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
    end = limit
    while end > header_line + 1 and (not lines[end - 1].strip() or _is_comment(lines[end - 1])):
        end -= 1
    return start, end


def _checked(text: str) -> str:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise QewError(f"the edited config would not parse: {error}", 1) from None
    return text


def mirror_blocks(text: str) -> list[tuple[int, int]]:
    lines = text.split("\n")
    headers = _headers(lines)
    return [_block(lines, headers, i) for i, (_, name, array) in enumerate(headers) if array and name == "mirror"]


def append_mirror(text: str, entries: list[tuple[str, str]]) -> str:
    """Add a `[[mirror]]` block of `key = literal` lines at the end of the file."""
    body = "\n".join(["[[mirror]]", *(f"{key} = {literal}" for key, literal in entries)]) + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return _checked(text + ("\n" if text.strip() else "") + body)


def remove_mirror(text: str, index: int) -> str:
    """Remove the `index`-th `[[mirror]]` block (with the blank line that set it apart)."""
    lines = text.split("\n")
    start, end = mirror_blocks(text)[index]
    if start > 0 and not lines[start - 1].strip() and (end >= len(lines) or not lines[end].strip()):
        start -= 1
    del lines[start:end]
    return _checked("\n".join(lines))


def set_key(text: str, table: str, key: str, literal: str, index: int | None = None) -> str:
    """Set `key = literal` in a table (the `index`-th `[[table]]` for arrays), adding the key or the table."""
    lines = text.split("\n")
    headers = _headers(lines)
    wanted = [i for i, (_, name, array) in enumerate(headers) if name == table and (index is not None) == array]
    if not wanted:
        return _checked(text.rstrip("\n") + f"\n\n[{table}]\n{key} = {literal}\n")
    position = wanted[index or 0]
    start, end = _block(lines, headers, position)
    for line_index in range(headers[position][0] + 1, end):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[line_index]):
            lines[line_index] = f"{key} = {literal}"
            return _checked("\n".join(lines))
    lines.insert(end, f"{key} = {literal}")
    return _checked("\n".join(lines))


def remove_key(text: str, table: str, key: str, index: int | None = None) -> str:
    lines = text.split("\n")
    headers = _headers(lines)
    wanted = [i for i, (_, name, array) in enumerate(headers) if name == table and (index is not None) == array]
    if not wanted:
        return text
    position = wanted[index or 0]
    _, end = _block(lines, headers, position)
    kept = [line for n, line in enumerate(lines)
            if not (headers[position][0] < n < end and re.match(rf"^\s*{re.escape(key)}\s*=", line))]
    return _checked("\n".join(kept))


def update(root: Path, transform: Callable[[str], str]) -> None:
    """Rewrite `<root>/.qew/config.toml` through `transform`, atomically; nothing is written if it raises."""
    path = root / ".qew" / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
        new = transform(text)
        if new != text:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(new, encoding="utf-8")
            temporary.replace(path)
    except (OSError, UnicodeError) as error:
        raise QewError(f"{path}: {error}", 1) from None
