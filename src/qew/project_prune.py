"""`qew project prune --from OWNER/REPO`: remove another repository's cards from a shared board.

Two mirror targets may point at one organisation board. Only cards whose issue belongs to exactly the named
repository (case-insensitive, never a prefix) are deleted; issues are never touched; the result is re-read.
"""

import re
from typing import Callable, Optional

from qew import forge_registry, ledger_config
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards
from qew.errors import QewError
from qew.forge import COLUMNS, Board, Forge, ForgeError, Unsupported
from qew.ledger_config import Config, Mirror
from qew.project_status import repository_of

_SOURCE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def prune(config: Config, wanted: Optional[str], source: str, build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    if not _SOURCE.match(source):
        raise QewError(f"`{source}` is not OWNER/REPO", 2)
    mirrors = choose(mirrors_with_boards(ledger_for(config), config), wanted)
    if len(mirrors) != 1:
        raise QewError("name the target to prune with --target", 2)
    mirror = mirrors[0]
    slug = ledger_config.mirror_slug(mirror)
    if source.casefold() == repository_of(mirror).casefold():
        raise QewError(f"{source} is the board's own repository; refusing to empty it", 2)
    if mirror.project is None:
        raise QewError(f"{slug} has no recorded or configured board; run `qew project ensure`", 2)
    board = Board(mirror.project, "", COLUMNS)
    forge = build(mirror)
    try:
        doomed = {card.id for card in forge.read_board_cards(board) if card.repository.casefold() == source.casefold()}
        for card_id in sorted(doomed):
            forge.delete_card(board, card_id)
        left = {card.id for card in forge.read_board_cards(board) if card.repository.casefold() == source.casefold()}
    except Unsupported:
        return f"{slug}: boards unsupported"
    except ForgeError as error:
        raise QewError(f"{slug}: {error.detail}", 1) from None
    if left:
        raise QewError(f"{slug}: indeterminate: {len(left)} card(s) of {source} remain after the delete: {' '.join(sorted(left))}", 1)
    return f"{slug}: removed {len(doomed)}"
