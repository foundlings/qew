"""Where a forge issue can link to an attachment's stored file in the ledger repository.

The issue never carries the bytes: it lists names and links to `items/ID/attachments/BLOB` on the ledger repository's
web view at the ledger branch. Blobs are immutable, so the link stays valid. With no web address known the issue lists
the names without links.
"""

from collections.abc import Callable
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

Link = Callable[[str, str], str | None]  # (item id, blob file name) -> URL or None

_SCP = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)([\w./~-]+?)(?:\.git)?/?$")
_URL = re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?([\w.-]+)(?::\d+)?/([\w./~-]+?)(?:\.git)?/?$")


def web_base(address: str) -> str | None:
    """`https://host/owner/repo` for a web address or a git clone URL (ssh, scp-style or https); None if unrecognised."""
    text = address.strip()
    for pattern in (_URL, _SCP):
        found = pattern.match(text)
        if found:
            return f"https://{found.group(1)}/{found.group(2)}"
    return None


def origin_of(ledger_path: Path) -> str:
    """The ledger checkout's `origin` URL, or "" when it has none or git cannot say."""
    try:
        done = subprocess.run(["git", "-C", str(ledger_path), "remote", "get-url", "origin"], capture_output=True, text=True,
                              check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def link_for(base: str | None, ref: str, blob_url: Callable[[str, str, str], str]) -> Link:
    """The resolver a mirror uses: `blob_url(base, ref, path)` is the backend's own web path scheme."""

    def link(item_id: str, blob: str) -> str | None:
        if base is None:
            return None
        return blob_url(base, ref, f"items/{quote(item_id)}/attachments/{quote(blob)}")

    return link
