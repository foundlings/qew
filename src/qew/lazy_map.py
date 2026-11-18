"""A read-only mapping whose values are built on first access.

A snapshot of a big ledger holds every item's events and folded state, and most commands look at a handful of items. The keys are known up
front, in the order given (iteration, `in`, `len` never build anything); a value is built by its function the first time it is read and kept.
"""

from collections.abc import Callable, Iterator, Mapping
from typing import override


class LazyMap[V](Mapping[str, V]):
    def __init__(self, order: list[str], built: dict[str, V], pending: dict[str, Callable[[], V]]) -> None:
        self._order = order
        self._known = set(order)
        self._built = built
        self._pending = pending

    @override
    def __getitem__(self, key: str) -> V:
        if key in self._built:
            return self._built[key]
        build = self._pending.get(key)
        if build is None:
            raise KeyError(key)
        value = build()
        self._built[key] = value
        self._pending.pop(key, None)
        return value

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._order)

    @override
    def __len__(self) -> int:
        return len(self._order)

    @override
    def __contains__(self, key: object) -> bool:
        return key in self._known

    def updated(self, replacements: Mapping[str, V]) -> LazyMap[V]:
        """A new map holding `replacements` over this one (new keys at the end); this map and its already built values are shared."""
        order = [*self._order, *(key for key in replacements if key not in self._known)]
        built = {**self._built, **replacements}
        pending = {key: build for key, build in self._pending.items() if key not in replacements}
        return LazyMap(order, built, pending)

    def built(self) -> int:
        """How many values have been built (for tests and traces)."""
        return len(self._built)
