"""The pull request description's block of declared items: a projection of the ledger, written by qew and never read back as input.

The block sits between two markers. A rewrite replaces only what is between them (every other byte of the body stays); a body with no block
gets one at its end. Titles and reasons are untrusted text: one line, no comment markers, and no `#N` that a forge could read as a closing
reference.
"""

import re

from qew.ledger_store import Snapshot
from qew.pr_links import ROLES, Declaration

BEGIN = "<!-- qew:links v1 -->"
END = "<!-- /qew:links -->"
_BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
_TITLE_LIMIT = 200


def _plain(text: str) -> str:
    one = " ".join(text.split()).replace("<!--", "").replace("-->", "")
    return re.sub(r"#(?=\d)", "＃", one)[:_TITLE_LIMIT]  # a full-width sign: visible, and no closing reference


def render(snapshot: Snapshot, declaration: Declaration, landed: list[str] | None = None) -> str:
    """The block for a declaration: one line per item with its role and title, or the no-item reason; after a merge, what was landed."""
    lines = [BEGIN, "### Ledger items", ""]
    if declaration.no_item is not None:
        lines.append(f"- no item: {_plain(declaration.no_item)}")
    for role in ROLES:
        for item_id in declaration.items(role):
            item = snapshot.items.get(item_id)
            lines.append(f"- {role} `{item_id}`" + (f": {_plain(item.title)}" if item is not None else ""))
    if len(lines) == 3:
        lines.append("- no declaration yet")
    if landed:
        lines += ["", "### Landed", ""] + [f"- {_plain(line)}" for line in landed]
    lines.append(END)
    return "\n".join(lines)


def current(body: str) -> str | None:
    """The block the body holds now, or None."""
    found = _BLOCK.search(body)
    return found.group(0) if found else None


def apply(body: str, block: str) -> str:
    """`body` with its block replaced by `block` (appended after a blank line when it has none); all other bytes are kept."""
    if _BLOCK.search(body):
        return _BLOCK.sub(lambda _: block, body, count=1)
    return body.rstrip("\n") + "\n\n" + block + "\n" if body.strip() else block + "\n"
