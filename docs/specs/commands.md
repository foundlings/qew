# Commands

Output is terse and fixed-format: `ID STATE pN kind title [@owner]`, ordered by priority then sortable id
(counter ids numerically) the same way in `ready`, `list` and `blocked`. Titles are flattened, bounded and
quoted or withheld (see security.md). `--json` gives machine output (see Machine output).
An item reference is a full ID, the same ignoring case, or a unique prefix. Exit codes: 0 ok, 1 invalid
ledger or forge failure, 2 refusal or usage, 3 environment.

## Machine output
Bounded answers (`ready`, `blocked`) print one envelope object; open-ended lists (`list`, `graph`,
`draft list`) print one object per line (NDJSON) with `schema_version` on each row; `show` prints one
object. Keys are sorted and separators compact, so output on the same ledger is byte-identical. Optional
data that is absent (owner, parent, needs, body) is omitted, never null. A read that cannot answer
(unreadable ledger, bad usage) prints nothing on stdout and one reason line on stderr. A ledger with an
unparseable item or event file, a duplicate id, history for no item or an illegal transition is not
readable: every read refuses it (exit 1) and names the file instead of listing what parsed. Dangling
needs and cycles do not stop a read; `ready` and `blocked` treat a dangling need as unmet.
Shapes (`required | optional`; a test fails when a command emits a key listed nowhere or omits a required one):
```
shape row: id kind labels priority state title | needs owner parent schema_version text_findings
shape envelope: items schema_version total truncated
shape blocked_row: blocked_by id kind labels priority state title | needs owner parent text_findings
shape edge: item schema_version | needs parent
shape show: id kind labels priority schema_version state title | blocks body children evidence mirrors needs owner parent text_findings
```

## Setup
- `qew --version`: print `qew X.Y.Z` (the installed package version); needs no repository, ledger or network.
- `qew init [--ledger PATH]`: write `.qew/config.toml` and the ledger's `qew-ledger.toml`.
- `qew hook install [--claude] [--codex] [--cursor] [--opencode] [--git] [--json]`: wire agents (none = all four; output order claude, codex, cursor, opencode, git). `--git` installs the tracked `.githooks/pre-push` and sets `core.hooksPath`; it is never part of the default.
- `qew hook remove [--claude] [--codex] [--cursor] [--opencode] [--git] [--json]`: remove exactly Qew's entries.
- `qew hook session-start --agent NAME`, `qew hook pre-tool --agent NAME`, `qew hook post-tool --agent NAME`:
  the events agents call; see hooks.md.
- `qew hook vcs-check (--pre-push | --revisions REVSET | --message-file FILE)`: refuse pushed commits whose `Refs: ID`
  trailers cite an item without evidence for that commit (exit 2, the exact `qew evidence` command on stderr); see hooks.md.

## Read
- `qew show ITEM [--json]`: header, state, owner, derived children and blocks, evidence, mirrors, body.
- `qew list [--kind K] [--state S] [--label L] [--json]`, `qew ready [--json]`, `qew blocked [--json]`,
  `qew graph [--format text|dot|mermaid] [--json]`: dependency edges (`A -> B`, A needs B) then parent edges
  (`A parent P`; dashed in dot and mermaid), sorted by id; labels are escaped for their syntax, a title hiding
  characters is withheld; `--json` and `--format` are exclusive. Readiness is derived from files; no network. `ready` lists by priority, then the
  longest chain it unblocks, then direct dependents, then id.
- `qew check`: schema, duplicate IDs, cycles, dangling needs, illegal transitions, completed without
  evidence, malformed mirrors. Writes nothing; exit 1 on any problem.

## Work
- `qew create [--kind K] --title T [--parent ID] [--needs ID...] [--label L] [--priority N] [--body TEXT]
  [--body-file F] [--quote Q] [--from-file F [--draft] [--dry-run]] [--json]`: a draft unless `--quote` carries a human's words
  (`"Name: 'their words'"`), which files it approved. `--from-file` takes a plan: JSON Lines, one JSON object or an array (or `-`
  for stdin). The whole plan is validated before the first write (exit 2, naming the line): regular UTF-8
  file, not a symlink; no unknown fields; unique `ref`s; `@ref` entries in `needs`/`parent` resolve in any
  order; no cycle, bad nesting or self edge; `id` (imports keep it), `created` allowed. `--body-file` refuses a symlink (exit 2). An entry with a valid
  `quote` is filed approved with its own words; `--quote` is the default for entries without one; an entry with
  no quote is a draft; `approved: true` needs a quote. `--draft` files every entry as a draft and refuses
  `approved: true` lines and `--quote`.
  `--dry-run` runs the full validation, writes and calls nothing, and prints the operations in apply order
  (`create` with `draft`, `adopt`, `parent`, `need`; `--json` adds `schema_version` 1 and `plan_sha256`).
  Re-running the same plan file adopts the items an earlier (killed) run already wrote instead of creating them
  again: each created item's first event carries `plan` (sha256 of the bytes), `key` (`ref` or `line-N`) and
  `source` (file name). Two items with one marker are `ambiguous`; an edited file over items of its earlier
  revision is `plan revision mismatch` (both exit 2, nothing written). `--json` is `{"created": {key: id},
  "adopted": [key]}`.
  `--import` (with `--from-file`) keeps each item's own `events` list, validated as a whole before any write;
  a `completed` event may carry `"provenance": "import"` and `original_ids`, the only way to complete
  without evidence. `qew complete` never writes it. `qew check` reports such items as
  `imported without evidence: N` (non-fatal).
- `qew set ITEM... [--title T] [--parent ID] [--needs ID...] [--labels L...] [--priority N] [--body TEXT]
  [--body-file F|-] [--kind K] [--add-label L] [--remove-label L] [--closed] [--json]`: edits the header; each change appends a `field` audit event. The whole result is validated
  against the graph before the first write (exit 2, nothing changed): no self edge, unknown or duplicate
  need, `needs` cycle (named), or nesting other than epic > issue > task (a task may also sit under an epic);
  a kind or parent change must keep existing children valid. Completed or closed work is edited only with
  `--closed`. Several items may share parent, needs, labels or priority; `--title` and `--body` take one item.
  `--add-label`/`--remove-label` are additive and idempotent (never with `--labels`); `--kind` is revalidated
  against the nesting rules; one `field` event per changed key, and the item is re-read and compared (exit 3
  on a mismatch).
  `qew create` applies the same edge rules before writing.
- `qew start ITEM`: claim approved work whose needs are completed. `qew release ITEM` gives it up.
- `qew evidence ITEM [--change ID]... [--vcs jj|git]`: record the code changes; default is the current one.
- `qew complete ITEM`: refused unless the item is claimed, every child is completed or closed, and (not for
  epics) an evidence event whose change IDs are all ancestors of the configured target branch exists.
  An epic needs at least one child.
- `qew close ITEM --reason not_planned|duplicate|superseded`, `qew reopen ITEM`.
- `qew release ITEM`: give up a claim (listed under start).

- `qew block ITEM --on BLOCKER [--closed] [--json]`, `qew unblock ITEM --from BLOCKER [--closed] [--json]`: add or
  remove one need, validated like `set` (cycle, self edge), a no-op (`unchanged`, no event) when the edge already
  is or is not there, one `field` audit event otherwise, read back after the write (mismatch is exit 3).
  Completed or closed items need `--closed`.
- `qew comment ITEM (--body TEXT | --body-file F)`: append a `comment` event to any item in any state (`-`
  reads stdin); the body is stored verbatim as data, must not be blank, and never changes state. `qew show` lists
  comments in order with actor and time.

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
- `qew sync [--target T] [--item ID] [--dry-run]`: push to every `[[mirror]]` target independently. On a target with a
  board each card's Status (the seven-status model) and Priority (P0-P4; larger priorities clamp to P4 with a
  warning) are set, read back from the forge a bounded number of times, and recorded only once visible (else the item
  is reported failed and retried next sync). A card moved by hand is moved back. Items that left the target's `select`
  lose their card (`pruned N`); their issues are never closed.
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
- `qew project ensure [--target T]`: create or repair the full board where the forge has boards: the seven
  Status options (colors, descriptions), the Priority field P0-P4 and eight views (Kanban, Table, Ready, Blocked,
  Active, Review, Drafts, Roadmap). Idempotent; a corrected view or kept foreign option is printed as a note; extra
  human views are left alone. An overlay mirror (non-empty `select`) prefixes every view filter with
  `label:qew:managed`; repository mode leaves them bare. The board id is recorded in `boards/` in the ledger and
  found by that id even if a human renamed the project. A forge without boards prints `boards unsupported`, exit 0.

## Pull requests
- `qew pr`: the group for the three commands below.
- `qew pr open ITEM [--target T] [--head BRANCH] [--base BRANCH] [--ready]`: draft by default, never a
  closing keyword; recorded as a `pr` mirror.
- `qew pr ready NUMBER [--target T]`; `qew pr merge NUMBER [--target T]`: refuses drafts, conflicts, red or
  pending checks and unrecorded pull requests; merges exactly the checked head. Completion stays `qew complete`.
