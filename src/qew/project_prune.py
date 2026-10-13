"""`qew project prune --from OWNER/REPO | --orphans`: remove another repository's cards, or the board's own cards no item owns.

Two mirror targets may point at one organisation board. Only cards whose issue belongs to exactly the named
repository (case-insensitive, never a prefix) are deleted; issues are never touched; the result is re-read.
`--orphans` instead deletes the cards of the board's own repository whose issue no ledger item mirrors (a stray issue that was
closed or moved still has its card).
"""

import re
from collections.abc import Callable

from qew import board_cards, forge_registry, ledger_config, project_status
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards
from qew.errors import QewError
from qew.forge import COLUMNS, Board, Forge, ForgeError, Unsupported
from qew.ledger_config import Config, Mirror
from qew.project_status import repository_of

_SOURCE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def prune(config: Config, wanted: str | None, source: str | None, build: Callable[[Mirror], Forge] = forge_registry.build,
          orphans: bool = False) -> str:
    if (source is not None) == orphans:
        raise QewError("name exactly one of --from OWNER/REPO and --orphans", 2)
    if source is not None and not _SOURCE.match(source):
        raise QewError(f"`{source}` is not OWNER/REPO", 2)
    ledger = ledger_for(config)
    mirrors = choose(mirrors_with_boards(ledger, config), wanted)
    if len(mirrors) != 1:
        raise QewError("name the target to prune with --target", 2)
    mirror = mirrors[0]
    slug = ledger_config.mirror_slug(mirror)
    if source is not None and source.casefold() == repository_of(mirror).casefold():
        raise QewError(f"{source} is the board's own repository; refusing to empty it", 2)
    if mirror.project is None:
        raise QewError(f"{slug} has no recorded or configured board; run `qew project ensure`", 2)
    board = Board(mirror.project, "", COLUMNS)
    forge = build(mirror)
    snapshot = ledger.snapshot()

    def unwanted() -> set[str]:
        cards = forge.read_board_cards(board)
        if source is not None:
            return {card.id for card in cards if card.repository.casefold() == source.casefold()}
        own = board_cards.local_cards(cards, repository_of(mirror))
        return {own[number].id for number in project_status.orphan_numbers(ledger, snapshot, slug, own)}

    try:
        doomed = unwanted()
        for card_id in sorted(doomed):
            forge.delete_card(board, card_id)
        left = unwanted()
    except Unsupported:
        return f"{slug}: boards unsupported"
    except ForgeError as error:
        raise QewError(f"{slug}: {error.detail}", 1) from None
    if left:
        raise QewError(f"{slug}: indeterminate: {len(left)} card(s) of {source or 'orphans'} remain after the delete: {' '.join(sorted(left))}", 1)
    return f"{slug}: removed {len(doomed)}"
