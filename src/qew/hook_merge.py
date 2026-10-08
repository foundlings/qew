"""Post-tool completion hint after `qew pr merge`."""

from pathlib import Path
from typing import Callable, Optional


def reminder(command: str, cwd: Optional[Path], build: Callable) -> Optional[str]:
    return None
