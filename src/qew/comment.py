"""Add a comment to an issue."""

from pathlib import Path
from typing import Optional

from qew.errors import QewError
from qew.github import comment_issue


def add_comment(repository: str, issue: int, body: Optional[str], body_file: Optional[str]) -> None:
    if issue <= 0:
        raise QewError("issue number must be positive", 2)
    if (body is None) == (body_file is None):
        raise QewError("pass exactly one of --body or --body-file", 2)
    if body_file is not None:
        path = Path(body_file)
        if path.is_symlink():
            raise QewError("body-file must not be a symlink", 2)
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"cannot read body-file: {error}", 2) from error
    if body is None or not body.strip():
        raise QewError("comment must not be empty", 2)
    comment_issue(repository, issue, body)
