"""Filters of the list read. Names fold ASCII case only: a Unicode fold would let a lookalike match."""

from collections.abc import Iterable

_ASCII_FOLD = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}


def fold(text: str) -> str:
    """Lower-case A-Z and nothing else (the Kelvin sign stays the Kelvin sign)."""
    return text.translate(_ASCII_FOLD)


def has_all_labels(labels: Iterable[str], wanted: Iterable[str]) -> bool:
    have = {fold(label) for label in labels}
    return all(fold(label) in have for label in wanted)
