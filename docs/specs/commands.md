# Commands

Output is terse and fixed-format: `ID STATE pN kind title [@owner]`. `--json` gives machine output.
An item reference is a full ID, the same ignoring case, or a unique prefix. Exit codes: 0 ok, 1 invalid
ledger or forge failure, 2 refusal or usage, 3 environment.

## Setup
- `qew init [--ledger PATH]`: write `.qew/config.toml` and the ledger's `qew-ledger.toml`.
- `qew hook install [--claude] [--codex] [--cursor] [--opencode] [--json]`: wire agents (none = all; output order claude, codex, cursor, opencode).
- `qew hook remove [--claude] [--codex] [--cursor] [--opencode] [--json]`: remove exactly Qew's entries.
- `qew hook session-start --agent NAME`, `qew hook pre-tool --agent NAME`, `qew hook post-tool --agent NAME`:
  the events agents call; see hooks.md.

## Read
- `qew show ITEM [--json]`: header, state, owner, derived children and blocks, evidence, mirrors, body.
- `qew list [--kind K] [--state S] [--label L] [--author LOGIN] [--json]`, `qew ready [--json]`, `qew blocked [--json]`,
  `qew graph [--json]`: readiness is derived from files; no network. `ready` lists by priority, then the
  longest chain it unblocks, then direct dependents, then id.
- `qew check [--json]`: schema, duplicate IDs, cycles, dangling needs, illegal transitions, completed without
  evidence, malformed mirrors, text findings, contradictions (claimed epic, completed item with open needs,
  open child of a completed epic), reserved labels. Writes nothing; exit 1 on any problem. `--json` prints
  `schema_version`, `valid`, `cycles` (each once, rotated to the smallest id, closed on it), `contradictions`,
  `text_findings`, `problems`.

- `qew inbox [--login L] [--count] [--json]`: `waiting` (drafts whose author is L) and `assigned` (items L has
  claimed), from the ledger only. `--count` prints `inbox: N waiting, M assigned`; with `--json` exit 2; a login
  that is not `[A-Za-z0-9][A-Za-z0-9_.-]{0,49}` exit 2. Login defaults to the configured actor.

## Work
- `qew create [--kind K] --title T [--parent ID] [--needs ID...] [--label L] [--priority N] [--body TEXT]
  [--body-file F] [--quote Q] [--from-file F]`: a draft unless `--quote` carries a human's words
  (`"Name: 'their words'"`), which files it approved. `--from-file` takes a JSON object or array (or `-`
  for stdin); items may carry `id` (imports keep it), `created`, `ref`, and `@ref` entries in `needs`/`parent`.
  `--import` (with `--from-file`) keeps each item's own `events` list, validated as a whole before any write;
  a `completed` event may carry `"provenance": "import"` and `original_ids`, the only way to complete
  without evidence. `qew complete` never writes it. `qew check` reports such items as
  `imported without evidence: N` (non-fatal).
- `qew set ITEM... [--title T] [--parent ID] [--needs ID...] [--labels L...] [--priority N] [--body TEXT]
  [--closed]`: edits the header; each change appends a `field` audit event. The whole result is validated
  against the graph before the first write (exit 2, nothing changed): no self edge, unknown or duplicate
  need, `needs` cycle (named), or nesting other than epic > issue > task (a task may also sit under an epic);
  a kind or parent change must keep existing children valid. Completed or closed work is edited only with
  `--closed`. Several items may share parent, needs, labels or priority; `--title` and `--body` take one item.
  `qew create` applies the same edge rules before writing.
- `qew start ITEM`: claim approved work whose needs are completed. `qew release ITEM` gives it up.
- `qew evidence ITEM [--change ID]... [--vcs jj|git]`: record the code changes; default is the current one.
- `qew complete ITEM`: refused unless the item is claimed, every child is completed or closed, and (not for
  epics) an evidence event whose change IDs are all ancestors of the configured target branch exists.
  An epic needs at least one child.
- `qew close ITEM --reason not_planned|duplicate|superseded`, `qew reopen ITEM`.
- `qew release ITEM`: give up a claim (listed under start).

## Drafts
- `qew draft`: the group for the three commands below.
- `qew draft list [--parent ID] [--json]`: open drafts by priority then id, with kind and parent;
  `--parent` keeps the direct children of one item. Files only, no network.
- `qew draft approve ITEM... [--tree] --quote Q [--json]`: only a human's quoted words release work. Duplicate
  references are one target. A named item that is not a draft is skipped and reported when another target
  remains; when none remains the command exits 2 `nothing to approve`. A closed root is refused before any
  write. `--tree` follows `parent` edges (never `needs`), visits each item once (at most 1000) and releases
  only drafts. Output is `ID approved` / `ID skipped (why)`; `--json` is `{"approved": [...], "skipped": [...]}`.
- `qew draft revert ITEM... [--tree] --reason TEXT --quote Q [--json]`: approved, unclaimed work back to draft
  (`ID draft`; `--json` `{"reverted": [...], "skipped": [...]}`). The reason must not be blank. Claimed work
  (any item in a `--tree`) is refused with exit 3 naming `qew release`; there is no force flag. Approved work
  with a pull request mirror that is not recorded as a draft is refused (exit 2); a draft pull request does not
  block. Drafts and closed items in a list or tree are skipped; a lone draft is `nothing to revert`.
- A move that fails after its first write exits 3 and prints `approved: IDS; not approved: IDS; rerun the same
  command to finish`, plus `qew sync` for the mirrors (`reverted:` / `not reverted:` for revert). Each event is
  read back; a state other than the intended one is exit 3, never success. Written events stay (no rollback).

## Mirrors
- `qew sync [--target T] [--item ID] [--dry-run]`: push to every `[[mirror]]` target independently.
  Refuses an invalid ledger. A mirror file is written only after the forge accepted the call.
- `qew status [--json]`: `ledger uncommitted=N unpushed=N mode=online|offline remote=ok|offline|none`, one `target SLUG pending=N synced=ISO|never` line per target, `divergences=N`. Read-only: it never fetches, pushes or recovers.
- `qew accept DIVERGENCE [--reason R] [--completed]`: apply a forge-side state change recorded by the pull. A forge close
  becomes `closed` (reason `not_planned` by default); `--completed` runs the normal completion gate instead and is refused
  without a claim and evidence. A reopen becomes `reopened`; title and label edits apply as field events. DIVERGENCE is the
  divergence event id (a unique prefix) or an item with exactly one open divergence.
- `qew reject DIVERGENCE`: keep the ledger value; the mirror is marked stale so the next push rewrites the forge.
- `qew import [--from TARGET | --forge F --repo URL [--api URL]] [--history]`: read a forge project into the ledger. Issues
  carrying qew's body marker restore their item (id, kind, priority, labels, parent, needs, state from the footer and managed
  labels); any other issue becomes a fresh draft (closed ones arrive closed). Idempotent; records mirrors. `--history` is
  accepted for closed foreign issues.
- `qew restore [--to TARGET | --forge F --repo URL [--api URL]]`: recreate the issues, links and board of a target from the ledger
  alone; mirror files whose remote issue is gone are forgotten first. Idempotent and resumable.
- `qew project`: the group for `ensure`.
- `qew project ensure [--target T]`: create the seven-status board where
  the forge has boards; the board id is recorded in `boards/` in the ledger.

## Pull requests
- `qew pr`: the group for the three commands below.
- `qew pr open ITEM [--target T] [--head BRANCH] [--base BRANCH] [--ready]`: draft by default, never a
  closing keyword; recorded as a `pr` mirror.
- `qew pr ready NUMBER [--target T]`; `qew pr merge NUMBER [--target T]`: refuses drafts, conflicts, red or
  pending checks and unrecorded pull requests; merges exactly the checked head. Completion stays `qew complete`.
