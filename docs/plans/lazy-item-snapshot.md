# Write verbs read only the items they touch

## Problem

Every write verb that calls `ledger.snapshot()` parses the whole ledger under the write lock: about 4 to 5 s of CPU on a
28,000-item ledger when the machine is idle, 17 to 24 s at load 30. Fifteen writers queue behind one lock, so a write that
should take seconds takes minutes. The listing and alias scan are fixed (7.2.5: an exact id goes straight to its item and the
alias map is stored). What is left is `Ledger._assemble`: list and stat every item file, parse every header, hand every verb a
snapshot of everything, when evidence, start, complete and `pr ready` read a handful of items.

## Design: no index to keep valid

A stored index of parsed items must be validated against the files, and the only exact validation is a stat of every item
file and events directory (0.5 s idle, 2 s at load 30), or a stored snapshot that is never reusable on a ledger written every
second (built and dropped, see 7.2.5). Instead a write verb gets a lazy snapshot:

- `Ledger.snapshot(lazy=True)` returns a `Snapshot` whose `items`, `folds` and `events` are `LazyMap`s over the id list. A value
  is read from the item's own files on first access (item file, that item's events directory), so it is exact by construction:
  a hand edit, a git merge or another writer's commit is seen because the file is read when asked for.
- The id list is the one derived thing. It comes from the stored alias map (`.qew/aliases.json`), which is exact for added and
  removed items because a new item directory changes its shard directory's modification time, minus the superseded copies.
- Code that iterates every item (graph walks, readiness) forces every value and costs what it costs today. So a verb is moved
  to the lazy snapshot only after its accesses are known: by key (`folds[id]`, `items[id]`), by walking parents
  (`holds_ancestor`), by its own needs (`ledger_graph.blockers`).
- Reads, `qew check` and every other command keep the full parse and refuse a damaged ledger. A lazy write verb fails loudly on
  the items it reads (an unreadable or mismatched file is an error at the access), and no longer on damage elsewhere: a broken
  item no longer stops every agent writing, `qew check` still names it.

## What needs the whole graph

`complete` asks which items are unblocked by it (`released_by`: dependents of the item, over all items' needs) and whether
its children are done (`children`: over all items' parents). Those are reverse edges. They stay on the full snapshot until the
second slice gives them an exact reverse-edge map (item headers' `parent` and `needs`, plus `edge` events), stored beside the
alias map with the item directory's modification time per item.

## Slices (hottest verb first; each ships with a before and after trace on a clone of the foundlings ledger)

1. Lazy snapshot, then `evidence` (and `retract`, `repair`), then `start`: no graph needed. Done when their trace shows
   `ledger snapshot (lazy)` and no `ledger snapshot (full)`, outputs are identical to the full snapshot's, and an unrelated damaged
   item does not stop them.
2. A reverse-edge map for `complete` (children, dependents, released items), validated per item directory.
3. `pr ready`, `pr open`, `pr merge`: read the items a pull request links, not the ledger.
4. The remaining verbs, only where a trace shows the full snapshot is the slow step.

## Fallbacks and guards

- `QEW_NO_LAZY=1` makes every verb take the full snapshot (the escape hatch for a suspected difference).
- A walk per verb compares its output and ledger writes under the lazy and the full snapshot on the same ledger.
- The lazy snapshot never caches across a write: a value read before the verb appends an event is not read again afterwards.
