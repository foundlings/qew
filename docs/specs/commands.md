# Commands

Output is terse and fixed-format: `ID STATE pN kind title [@owner]`. `--json` gives machine output.
An item reference is a full ID, the same ignoring case, or a unique prefix. Exit codes: 0 ok, 1 invalid
ledger or forge failure, 2 refusal or usage, 3 environment.

## Setup
- `qew init [--ledger PATH]`: write `.qew/config.toml` and the ledger's `qew-ledger.toml`.
- `qew hook install [--claude] [--codex] [--cursor] [--opencode]`: wire agents (none = all).
- `qew hook remove [--claude] [--codex] [--cursor] [--opencode]`: remove exactly Qew's entries.
- `qew hook session-start --agent NAME`, `qew hook pre-tool --agent NAME`, `qew hook post-tool --agent NAME`:
  the events agents call; see hooks.md.

## Read
- `qew show ITEM [--json]`: header, state, owner, derived children and blocks, evidence, mirrors, body.
- `qew list [--kind K] [--state S] [--label L] [--json]`, `qew ready [--json]`, `qew blocked [--json]`,
  `qew graph [--json]`: readiness is derived from files; no network.
- `qew check`: schema, duplicate IDs, cycles, dangling needs, illegal transitions, completed without
  evidence, malformed mirrors. Writes nothing; exit 1 on any problem.

## Work
- `qew create [--kind K] --title T [--parent ID] [--needs ID...] [--label L] [--priority N] [--body TEXT]
  [--body-file F] [--quote Q] [--from-file F]`: a draft unless `--quote` carries a human's words
  (`"Name: 'their words'"`), which files it approved. `--from-file` takes a JSON object or array (or `-`
  for stdin); items may carry `id` (imports keep it), `created`, `ref`, and `@ref` entries in `needs`/`parent`.
  `--import` (with `--from-file`) keeps each item's own `events` list, validated as a whole before any write;
  a `completed` event may carry `"provenance": "import"` and `original_ids`, the only way to complete
  without evidence. `qew complete` never writes it. `qew check` reports such items as
  `imported without evidence: N` (non-fatal).
- `qew set ITEM [--title T] [--parent ID] [--needs ID...] [--labels L...] [--priority N] [--body TEXT]`:
  edits the header; each change appends a `field` audit event.
- `qew start ITEM`: claim approved work whose needs are completed. `qew release ITEM` gives it up.
- `qew evidence ITEM [--change ID]... [--vcs jj|git]`: record the code changes; default is the current one.
- `qew complete ITEM`: refused unless the item is claimed, every child is completed or closed, and (not for
  epics) an evidence event whose change IDs are all ancestors of the configured target branch exists.
  An epic needs at least one child.
- `qew close ITEM --reason not_planned|duplicate|superseded`, `qew reopen ITEM`.
- `qew release ITEM`: give up a claim (listed under start).

## Drafts
- `qew draft`: the group for the three commands below.
- `qew draft list [--json]`.
- `qew draft approve ITEM... [--tree] --quote Q`: only a human's quoted words release work; `--tree`
  releases the drafts below the items too.
- `qew draft revert ITEM... --reason TEXT --quote Q`: approved, unclaimed work back to draft.

## Mirrors
- `qew sync [--target T] [--item ID] [--dry-run]`: push to every `[[mirror]]` target independently.
  Refuses an invalid ledger. A mirror file is written only after the forge accepted the call.
- `qew status [--json]`: `ledger uncommitted=N unpushed=N mode=online|offline remote=ok|offline|none`, one `target SLUG pending=N synced=ISO|never` line per target, `divergences=N`. Read-only: it never fetches, pushes or recovers.
- `qew project`: the group for `ensure`.
- `qew project ensure [--target T]`: create the Draft / Ready / In progress / In review / Done board where
  the forge has boards; the board id is recorded in `boards/` in the ledger.

## Pull requests
- `qew pr`: the group for the three commands below.
- `qew pr open ITEM [--target T] [--head BRANCH] [--base BRANCH] [--ready]`: draft by default, never a
  closing keyword; recorded as a `pr` mirror.
- `qew pr ready NUMBER [--target T]`; `qew pr merge NUMBER [--target T]`: refuses drafts, conflicts, red or
  pending checks and unrecorded pull requests; merges exactly the checked head. Completion stays `qew complete`.
