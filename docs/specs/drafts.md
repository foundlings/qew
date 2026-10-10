# Drafts

Agents file work straight into the queue (approved, `filed_by` the creator; Ready, or Backlog while it has unmet needs). A draft exists only when a human asks for one (`--draft`, or `approved: false` in a plan), and only a human's quoted words release it. A draft is never ready, never startable
and never completable; `qew list` still shows it (the ledger is a database, not a work queue).

- Only a human's words release work: `Name: 'their words'`, one line, at least 12 characters, no placeholder.
  The same check guards `qew create --quote`, `--from-file` entries, `qew draft approve` and `qew draft revert`;
  it runs before any item is resolved or file written (exit 2).
- `qew draft approve ITEM... [--tree] --quote Q` appends `approved` events. `--tree` follows `parent` edges
  only, visits each item once (at most 1000) and releases drafts only. Items that are not drafts are skipped and
  reported; nothing left to approve is exit 2. A closed root is refused.
- `qew draft revert ITEM... [--tree] --reason TEXT --quote Q` returns approved, unclaimed work to draft. There
  is no force flag: claimed work is refused (exit 3) and the message names `qew release ITEM`; work whose pull
  request is not a draft is refused (exit 2). Draft pull requests do not block.
- `qew draft list [--parent ID]` shows open drafts by priority then id with kind and parent.
- State comes only from events. Editing an item header or a forge-side label never releases a draft.
- A move that fails midway exits 3 and lists what moved and what did not; the rerun skips what already moved.
- The quote that released work is shown by `qew show`; discussion happens with `qew comment`, which never
  changes state.

## Decisions
- No `qew plan create`: `qew create --kind epic` then `qew set ITEM... --parent EPIC` (several items, all
  validated before the first write) files a plan and attaches existing items without a second command. A failed
  attach cannot duplicate the plan because the epic already exists and `set` is re-runnable.
- No structured body composition (`where`, `fix`, `done-when`, `--edit`): no current requirement. Bodies are
  `--body`, `--body-file` or a `body` field; `done-when` items are child tasks filed with the plan.
