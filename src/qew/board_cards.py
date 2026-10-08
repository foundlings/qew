"""Rules every consumer of a board's cards shares (status, prune, the offline board)."""

from typing import Dict, Iterable

from qew.errors import QewError
from qew.forge import Card


class DuplicateCards(QewError):
    """Two different cards stand for one issue: a real duplicate, never a pagination overlap."""


def local_cards(cards: Iterable[Card], repository: str) -> Dict[int, Card]:
    return {}
