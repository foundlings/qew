"""Safe local editor input for long-form issue bodies."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from qew.errors import QewError


def edit_body() -> str:
    command = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if command is None or not command.strip():
        raise QewError("--edit requires VISUAL or EDITOR", 2)
    try:
        arguments = shlex.split(command)
    except ValueError as error:
        raise QewError(f"invalid editor command: {error}", 2) from error
    if not arguments:
        raise QewError("--edit requires a non-empty editor command", 2)

    with tempfile.TemporaryDirectory(prefix="qew-edit-") as directory:
        path = Path(directory) / "body.md"
        path.write_text("", encoding="utf-8")
        try:
            completed = subprocess.run(
                [*arguments, str(path)],
                check=False,
            )
        except OSError as error:
            raise QewError(f"cannot run editor: {error}", 2) from error
        if completed.returncode != 0:
            raise QewError(f"editor exited with status {completed.returncode}", 2)
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"cannot read edited body: {error}", 2) from error
        if not body.strip():
            raise QewError("edited body must not be empty", 2)
        return body
