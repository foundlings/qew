"""A read-only mapping whose values are built on first access.

A snapshot of a big ledger holds every item's events and folded state, and most commands look at a handful of items. The keys are known up
front, in the order given (iteration, `in`, `len` never build anything); a value is built by its function the first time it is read and kept.
"""

from collections.abc import Callable, ItemsView, Iterator, Mapping, ValuesView
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


class AdaptiveMap[V](LazyMap[V]):
    """A lazy map that is never worse than the full build it stands in for.

    One value at a time is cheap for a verb that reads a few items. A verb that reads them all (a graph walk, `dict(map)`, `.values()`)
    would read each one separately, several times slower than building everything at once. After `after` single reads, or at the first
    bulk iteration, `everything` (the full build, made once) answers instead, and every later read comes from it."""

    def __init__(self, order: list[str], built: dict[str, V], pending: dict[str, Callable[[], V]],
                 everything: Callable[[], Mapping[str, V]], after: int) -> None:
        super().__init__(order, built, pending)
        self._everything = everything
        self._after = after
        self._singles = 0
        self._all: Mapping[str, V] | None = None

    def _bulk(self) -> Mapping[str, V]:
        if self._all is None:
            self._all = self._everything()
        return self._all

    @override
    def __getitem__(self, key: str) -> V:
        if self._all is not None:
            return self._all[key]
        if key not in self._built and key in self._known:
            self._singles += 1
            if self._singles > self._after:
                return self._bulk()[key]
        return super().__getitem__(key)

    @override
    def values(self) -> ValuesView[V]:
        return self._bulk().values()

    @override
    def items(self) -> ItemsView[str, V]:
        return self._bulk().items()
