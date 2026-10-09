"""Rules every consumer of a board's cards shares (status, prune, the offline board)."""

from typing import Dict, Iterable

from qew.errors import QewError
from qew.forge import Card


class DuplicateCards(QewError):
    """Two different cards stand for one issue: a real duplicate, never a pagination overlap."""


def local_cards(cards: Iterable[Card], repository: str) -> Dict[int, Card]:
    """The cards of `repository` by issue number.

    A card of another repository is ignored (a shared board's numbers collide and a foreign card never
    stands in for a missing local one); one card id listed twice is a pagination overlap and counts once;
    two different card ids for one issue are a real duplicate and an error naming both.
    """
    found: Dict[int, Card] = {}
    for card in cards:
        if card.repository.casefold() != repository.casefold():
            continue
        earlier = found.get(card.number)
        if earlier is not None and earlier.id != card.id:
            raise DuplicateCards(f"issue #{card.number} has two cards on the board: {earlier.id} and {card.id}", 1)
        found[card.number] = card
    return found
