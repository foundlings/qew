"""Body authoring: an editor on a private temp file, or a body file, never a shell."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from qew.errors import QewError


def edit_body() -> str:
    """Run $VISUAL, else $EDITOR, on an empty temp file and return what it wrote."""
    command = (os.environ.get("VISUAL") or "").strip() or (os.environ.get("EDITOR") or "").strip()
    if not command:
        raise QewError("--edit needs an editor: set VISUAL or EDITOR", 2)
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise QewError(f"cannot parse the editor command: {error}", 2) from None
    with tempfile.TemporaryDirectory(prefix="qew-edit-") as directory:
        path = Path(directory) / "body.md"
        path.write_text("", encoding="utf-8")
        try:
            finished = subprocess.run([*argv, str(path)], check=False)
        except OSError as error:
            raise QewError(f"cannot run the editor: {error}", 2) from None
        if finished.returncode != 0:
            raise QewError(f"the editor exited with status {finished.returncode}; nothing was written", 2)
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"cannot read the editor's file: {error}", 2) from None
    if not body.strip():
        raise QewError("the editor left an empty body; nothing was written", 2)
    return body


def read_body_file(path: str) -> str:
    """The text of a regular, non-empty body file (a symlink is refused)."""
    target = Path(path)
    if target.is_symlink():
        raise QewError(f"{path} is a symlink; refusing to read a body through it", 2)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {path}: {error}", 2) from None
    if not text.strip():
        raise QewError(f"{path} is empty", 2)
    return text
