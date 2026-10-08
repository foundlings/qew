# Commands

Output is terse and fixed-format: `ID STATE pN kind title [@owner]`, ordered by priority then sortable id
(counter ids numerically) the same way in `ready`, `list` and `blocked`. Titles are flattened, bounded and
quoted or withheld (see security.md). `--json` gives machine output (see Machine output).
An item reference is a full ID, the same ignoring case, or a unique prefix. Exit codes: 0 ok, 1 invalid
ledger or forge failure, 2 refusal or usage, 3 environment.
The actor written into events is `QEW_ACTOR`, else `USER`; it must be one word without spaces, commas or control
characters (exit 3 otherwise) and `qew check` flags events whose actor is not such a token.

## Machine output
Bounded answers (`ready`, `blocked`) print one envelope object; open-ended lists (`list`, `graph`,
`draft list`, `triage`) print one object per line (NDJSON) with `schema_version` on each row; `show` prints one
object. Keys are sorted and separators compact, so output on the same ledger is byte-identical. Optional
data that is absent (owner, parent, needs, body) is omitted, never null. A read that cannot answer
(unreadable ledger, bad usage) prints nothing on stdout and one reason line on stderr. A ledger with an
unparseable item or event file, a duplicate id, history for no item or an illegal transition is not
readable: every read refuses it (exit 1) and names the file instead of listing what parsed. Dangling
needs and cycles do not stop a read; `ready` and `blocked` treat a dangling need as unmet.
A `text_findings` entry is sparse: `{"source": "body", "zero_width": true}`, with `index` (and `author`
for a comment) where the source repeats; the kinds are `zero_width`, `bidi_control`, `unicode_tags`, `confusable`
and `control`.
Shapes (`required | optional`; a test fails when a command emits a key listed nowhere or omits a required one):
```
shape row: id kind labels priority state title | body children_done children_open completable needs owner parent pr schema_version section text_findings tracks work_state
shape envelope: items schema_version total truncated
shape blocked_row: blocked_by id kind labels priority state title | chain needs owner parent pr root text_findings tracks
shape edge: item schema_version | needs parent
shape show: author draft id kind labels priority schema_version state title | approvals blocked_by blocking body children children_done children_drafts children_open comments complete_command completable evidence mirrors needs next owner parent text_findings
shape triage_row: body id kind labels missing priority schema_version state title | needs owner parent text_findings
shape check: contradictions cycles problems schema_version text_findings valid
shape inbox_reply: at author excerpt id schema_version section
shape prime: additional_context context_bytes schema_version
```

## Setup
- `qew --version`: print `qew X.Y.Z` (the installed package version); needs no repository, ledger or network.
- `qew init [--ledger PATH]`: write `.qew/config.toml` and the ledger's `qew-ledger.toml` once, at the repository
  root (nearest `.git` or `.jj`; outside one, exit 2), atomically and never through a symlink (exit 2). Prints
  `created PATH` lines plus the trust guidance; a rerun prints `already initialized` and changes nothing.
- `qew hook install [--claude] [--codex] [--cursor] [--opencode] [--git] [--json]`: wire agents (none = all four; output order claude, codex, cursor, opencode, git). `--git` installs the tracked `.githooks/pre-push` and sets `core.hooksPath`; it is never part of the default.
- `qew hook remove [--claude] [--codex] [--cursor] [--opencode] [--git] [--json]`: remove exactly Qew's entries.
- `qew hook session-start --agent NAME`, `qew hook pre-tool --agent NAME`, `qew hook post-tool --agent NAME`:
  the events agents call; see hooks.md.
- `qew hook vcs-check (--pre-push | --revisions REVSET | --message-file FILE)`: refuse pushed commits whose `Refs: ID`
  trailers cite an item without evidence for that commit (exit 2, the exact `qew evidence` command on stderr); see hooks.md.

## Read
- `qew show ITEM [--json]`: the first line is the row (`ID STATE pN kind title`), so a draft says draft; then
  header, state, owner, evidence, mirrors, comments (index, author, time, text) and body. JSON adds
  `draft`, and neighbours as `{id, state, title}` objects (`state: missing` for a dangling edge):
  `blocked_by` (unmet needs), `blocking`, `children`, `parent`. An epic also gets `children_open`,
  `children_done`, `children_drafts` and `completable`, a text `progress: 0/3 completed, 2 drafts` line (plus
  `(draft epic)`), `next` (its highest-priority ready child, text `next: ID pN title`) and
  `complete_command` (null unless the epic is claimed with every child completed or closed). `text_findings`
  lists hidden-text findings of the title, body, labels and comments; a title with any is withheld in rows.
  A `tracks:` or `pr:` link prints `KEY: ID URL...`, the linked item's forge URLs read from its `mirrors/` files (header values
  are ledger ids only; `check` flags a URL, a forge number or an unknown id there).
- `qew list [--kind K] [--state S] [--work-state W] [--label L]... [--owner NAME] [--author NAME] [--parent ID]
  [--limit N] [--json]`: actionable work first. Every `--label` must match; labels and names fold ASCII case
  only (a Kelvin-sign lookalike never matches `k`). `--owner` is the current claimant, `--author` the actor
  of the creating event. `work_state` (JSON rows) is the derived class, in list order: `ready`, `blocked`
  (open work with an unmet need, claimed or not: a stuck claim is the more urgent fact), `claimed`, `plan`
  (an epic), `needs_decision` (a draft), `completed`, `closed`; then priority, then id. A filter that
  matches nothing prints nothing and exits 0; `--limit` cuts with a stderr warning. `--bodies` also prints the
  body of each non-draft item (JSON `body`): the explicit inspection; no other list prints bodies.
  `qew list --kind epic` rows carry `children_open`, `children_done`, `completable` (not closed or completed, at
  least one child, none open) and, only when completable and the title was not rewritten, `-> qew complete ID`;
  closed epics are left out unless `--state`/`--work-state` asks for them.
- `qew ready [--limit N] [--json]`: at most 30 items by default, `--limit 0` for all; a cut is reported as
  `truncated` with `total` in JSON and on stderr. A negative or non-integer limit is a usage error (exit 2).
  Ordered by priority, then the longest chain it unblocks, then direct dependents, then id.
- `qew blocked [--chains] [--json]`: approved or claimed work with unmet needs; JSON `blocked_by` holds
  `{id, state, title}` per unmet need (`closed`, or `missing` for a need that does not exist).
  `--chains` adds, under each row,
  `  chain: ITEM -> NEED -> ... [root STATE]`: the longest chain of unmet needs (ties: lowest id) to a root blocker
  that has no unmet needs, is closed, or is `dangling` (no such item); `--json` rows gain `chain` and `root`.
  A needs cycle is refused (exit 1).
- `qew graph [--format text|dot|mermaid] [--json]`: dependency edges (`A -> B`, A needs B) then parent edges
  (`A parent P`; dashed in dot and mermaid), sorted by id; labels are escaped for their syntax, a title hiding
  characters is withheld; `--json` and `--format` are exclusive. Readiness is derived from files; no network.
- `qew plan status [EPIC] [--json]`: roll up the direct children of one epic (every open epic when none is named)
  from files: counts `total draft ready blocked claimed closed`, `next` (the lowest priority number then id among
  ready children, never a draft or blocked child), and `completable` (non-draft epic, at least one child, every
  child completed or closed) with `complete_command` `qew complete ID`. A draft epic prints `(draft plan)`. Text
  is `ID state title` then `N/M closed` (`, K drafts` only when K > 0). A non-epic or unknown id is exit 2; a
  dangling need or illegal event order is exit 1 with no rollup. `--json` has `schema_version` 1.
- `qew check [--json]`: schema, duplicate IDs, cycles, dangling needs, illegal transitions, completed without
  evidence, malformed mirrors, reserved labels. Graph invariants, each named with its file and counted on a final
  `graph invariants: orphans=N cross_epic=N depth=N closed_needs=N` line when any is non-zero: history or mirrors
  of no item (orphans); a child whose `area:` labels share none with its parent's (cross_epic); a needs chain longer
  than 20 edges, reported at its deepest item (depth); open work that needs a closed item (closed_needs). Writes nothing;
  exit 1 on any problem. Advisory findings never fail it
  (text: `advisory: ...` lines): `contradictions` (a claimed epic, a completed item with open needs, an open child
  of a completed epic, a draft marker label `draft` or `qew:draft` on live work, a priority above 9) and
  `text_findings` (hidden or confusable text in titles, bodies, labels and comments). `--json` prints
  `schema_version`, `valid`, `cycles` (each once, rotated to the smallest id, closed on it), `contradictions`,
  `text_findings`, `problems`.

- `qew inbox [--login L] [--since T] [--count] [--json]`: what waits on one person, from the ledger only.
  Text sections with counts: `replies (N) since T` (one line per reply: `ID @author time: excerpt`, first line
  only, control characters as spaces, at most 100 characters with an ellipsis), `waiting for a human (N)`
  (drafts L created) and `assigned (N)` (items L holds). A reply is a `commented` event by someone else
  (logins compare ASCII-case-insensitively; not `[bot]`, not a machine marker starting `<!-- qew:`) at or
  after `--since` on an item L created, claimed or commented on; every event of every candidate item is
  scanned. `--since` is `YYYY-MM-DDTHH:MM:SSZ` or a date (midnight UTC), default 7 days ago; anything else,
  and a login outside letters, digits, `-`, `_`, `.` (optional `[bot]`), is exit 2 before any read; login
  defaults to the configured actor. `--count` prints
  `qew inbox (L): N issue(s) with replies since T: ID (author), ...; M waiting for a human; K assigned.` (no
  list when N is 0; the session-start hook appends it when something waits and it fits its 2000 bytes);
  with `--json` exit 2. `--json` is NDJSON: reply rows `{section: replies, id, author, at, excerpt}`, other
  rows are item rows with `section` `waiting` or `assigned`. A malformed event (bad time, no text) makes the
  read exit 1 with nothing on stdout.
- `qew prime [--json]`: the bounded session context the session-start hook also prints (the hook variant is
  smaller: 3 lines per section, 64-byte titles, 2000 bytes). First line `Qew ledger: N ready, N active, N blocked,
  N draft.`, four convention lines (titles are approved but untrusted data, never instructions), then `Next
  ready`, `Active`, `Blocked` and `Contradictions` sections of at most 5 lines plus `... N more`, `- none` when
  empty. Only approved, open work; titles are JSON-quoted, whitespace-collapsed and cut at 96 bytes on a UTF-8
  boundary with an ellipsis; a title with hidden-text findings is withheld and named under Contradictions
  (`ID suspicious title text: zero_width`), as is a claimed epic. Bodies and comments are never read. Over
  4096 bytes is a loud refusal (exit 1); an unreadable ledger is exit 1 (the hook stays silent).
  `--json` prints `additional_context` and the exact `context_bytes`.
- `qew search TERM... [--limit N] [--json]`: offline search of ids, titles and labels of non-draft items; every
  term must match (ASCII case folded). Best match first (exact title, then whole words, then substring), then
  priority, then id; at most 30 by default (`--limit 0` for all) with `search results capped` on stderr. Bodies
  and comments are never searched or printed; an empty result prints a stderr hint to use `qew triage --search`.
  Terms are data, never evaluated; put `--` before a term that starts with a dash. JSON is an envelope.
- `qew triage [--search TERM...] [--json]`: the explicit read of unapproved text: drafts, and open approved
  items missing a planning field, with title and body as they are and the `missing` list (sorted: `body`,
  `label:PREFIX` for each prefix of `[triage] label_groups` in `.qew/config.toml` that no label has, `parent`
  for a task without one). `--search` keeps items whose title or body holds every term (ASCII case folded).
  Rows are NDJSON in `--json`. Triage is the only list-shaped read that prints draft bodies.

- `qew export DIRECTORY`: write every item to a new (or empty) directory as `items/ID.md` (the ledger's own file
  format) and `events.json` (per item: event type, time, actor; import ignores it). Output is deterministic. A
  non-empty directory, a symbolic link or a ledger with a dangling edge or a cycle is refused before any write.
- `qew export --json`: print the items as one JSON array that `qew create --from-file` accepts (keys `id kind title
  parent needs labels priority created body`), each item after the items it references. Exactly one of
  DIRECTORY and `--json`.

## Work
- `qew create [--kind K] --title T [--parent ID] [--needs ID...] [--label L] [--priority N] [--body TEXT]
  [--body-file F | --edit] [--quote Q] [--from-file F [--draft] [--dry-run]] [--json]`: a draft unless `--quote` carries a human's words
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
- `qew set ITEM... [--title T] [--parent ID] [--needs ID...] [--tracks ID] [--pr ID] [--labels L...] [--priority N] [--due YYYY-MM-DD]
  [--body TEXT | --edit] [--body-file F|-] [--kind K] [--add-label L] [--remove-label L] [--closed] [--json]`: edits the header; each change appends a `field` audit event. The whole result is validated
  against the graph before the first write (exit 2, nothing changed): no self edge, unknown or duplicate
  need, `needs` cycle (named), or nesting other than epic > issue > task (a task may also sit under an epic);
  a kind or parent change must keep existing children valid. Completed or closed work is edited only with
  `--closed`. Several items may share parent, needs, labels or priority; `--title` and `--body` take one item.
  `--add-label`/`--remove-label` are additive and idempotent (never with `--labels`); `--kind` is revalidated
  against the nesting rules; one `field` event per changed key, and the item is re-read and compared (exit 3
  on a mismatch).
  `qew create` applies the same edge rules before writing.
  No change prints `unchanged` and writes nothing.
  `--edit` runs `$VISUAL`, else `$EDITOR` (split, never via a shell) on an empty temp file in a private
  directory, removed on every path; unset, blank, unparseable, a non-zero exit or an empty result refuse with
  exit 2. `--body-file` refuses a symlink and an empty file.
- `qew comment ITEM (--body TEXT | --body-file F)`: append a `commented` event (text kept verbatim, `-` reads
  stdin); exactly one source, non-blank, else exit 2 before any write. Comment text is data and never a state change.
- `qew start ITEM`: claim approved work whose needs are completed. `qew release ITEM` gives it up.
- `qew evidence ITEM [--change ID]... [--vcs jj|git]`: record the code changes; default is the current one.
- `qew complete ITEM`: refused unless the item is claimed, every child is completed or closed, and (not for
  epics) an evidence event whose change IDs are all ancestors of the configured target branch exists.
  An epic needs at least one child ("no completed work") and is refused with a count of open children.
  Completing completed work is a quiet no-op (`completed (unchanged)`, no second event).
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
  lose their card (`pruned N`); their issues are never closed. `--item ID` also refreshes the mirrored items whose
  card that item moves: its direct dependents and its parent epics (each card written only when it differs).
  A target with `milestones = true` also mirrors each approved, non-draft epic as a forge milestone (title, `Approved:`
  plus the approving quote, the epic's `due` date) and assigns its approved children; description and due date are
  rewritten only on drift, an issue already in another milestone is reported `not moved`, a milestone closes only when
  its epic is completed and no open issue sits in it (and is reopened if the epic is), every write is read back, and a
  forge without milestones is `skipped milestones`. Reports `milestones created N updated N unchanged N`.
  Refuses an invalid ledger. A mirror file is written only after the forge accepted the call.
  After the ledger commit, `sync` pushes the code repo (`[code] target`) and the ledger repo (`[replication] branch`)
  to every target through a git remote `qew-<slug>` (never forced). The clone URLs are the `[[mirror]]` keys `git_url`
  (code repo) and `ledger_git_url` (ledger repo), never derived from `repo`; a target with neither gets one
  `repos SLUG: set git_url ...` report line and no remote. Each target and repo is isolated: `repos SLUG code|ledger:
  failed: WHY` reports an unreachable or rejecting remote without blocking the rest. The forge repositories must exist.
- `qew mirror add FORGE REPO [--read-only] [--api URL]`, `qew mirror remove TARGET`, `qew mirror list [--json]`: edit the
  `[[mirror]]` entries of `.qew/config.toml` line by line; comments and every other table stay byte for byte. `add` refuses
  an unknown forge, an invalid URL and a repeated target (exit 2). `remove` refuses the primary forge (exit 2) and keeps
  the target's mirror files. A target is one-way (`read-only`) when marked `read_only = true` or when a primary is set
  and it is not the primary: pull never imports its content edits, new issues or comments, and only flags a close or
  reopen as a divergence. `list` prints `SLUG primary|two-way|read-only FORGE REPO`.
- `qew ledger fork --to REMOTE [--primary TARGET]`: push a copy of the ledger to the empty git repository REMOTE under a
  new identity: `ledger_id` (random) and `forked_from = "<origin id or name>@<commit>"` in `qew-ledger.toml`. Items and
  their events keep their ids; `mirrors/`, `boards/`, the claim in force on open work (the item is approved again) and
  unresolved divergences are not carried. `--primary` prints the `[authority] primary` config for the fork's code repo.
  Ledgers of different identity never merge: a fetch that would bring a fork into its origin (or back) is refused
  (exit 1, nothing merged). A ledger without `ledger_id` merges only with one without.
- `qew forge migrate --to TARGET [--remove-old]`: make a configured target the primary forge. It restores the ledger onto
  the target (the ordinary restore: issues, board; a pull request of another target becomes a `Pull request: URL`
  comment on the issue), verifies that every selected item has its issue there, and only then switches
  `[authority] primary` and marks the old primary `read_only = true` (`--remove-old` drops its block) in one atomic config
  write. Any failure or a missing issue is exit 1 or 3 with the config untouched; a rerun resumes from the mirror files
  without duplicates. Migrating to the current primary prints `already the primary`. The ledger is locked meanwhile.
- `qew board [--json] [--compact] [--sort KEY[:desc]] [--group-by KEY] [--filter TERMS]`: the offline kanban, derived from the ledger alone (no forge, no network). Columns
  are the seven statuses of `sync_plan.column`, stacked top to bottom (`Draft (2)` then one card line per card:
  short id, `[pN]`, title, `@owner`, `#label`), cards by priority then id. Titles are made safe (hidden characters
  withhold the title); lines are cut to the terminal width (`COLUMNS`); color only on a TTY without `NO_COLOR`.
  `--json` prints `{"version":1,"board":NAME,"groups":[{"name","count","cards":[...]}]}` (card fields: id, short,
  title, status, state, kind, priority, owner, labels, epic, parent, created, updated). `--compact` prints a
  `# Column N` line per column then `SHORTID pN OWNER|- TITLE` (title cut to 40 characters).
  `--sort` is `priority` (default), `updated` (newest event), `created` or `title`, optionally `:desc`; ties by id.
  `--group-by` is `column` (default), `owner`, `label`, `epic` or `parent`: sections replace the columns, each card
  appears under every label it carries and under `(none)` when it has no value for the key. `--filter` takes
  `label:X kind:X state:X priority:N owner:X epic:ID parent:ID status:X` (spaces and, commas or); an unknown key or a
  malformed term exits 2 naming the valid ones.
- `qew board --name NAME`: use the named board file; bare `qew board` uses the file named `default` when there is
  one. A board file's columns, sort, group and filter are the defaults; a flag replaces the one it names for that run.
- `qew board create NAME [--columns A,B] [--sort K] [--group-by K] [--filter T]`: write `views/NAME.json` in the
  ledger (one file per board, so branches merge). NAME is lowercase letters, digits and dashes; an existing name,
  an unknown or repeated column or an invalid sort/group/filter exits 2 and writes nothing.
- `qew board list`: one line per board file. `qew board set NAME [same options]`: change that file's defaults.
  `qew check` fails a board file whose name differs from its file name or whose columns are unknown or repeated.
- `qew board columns`: one `Column N` line per column.
- `qew status [--json]`: `ledger uncommitted=N unpushed=N mode=online|offline remote=ok|offline|none`, one `target SLUG pending=N synced=ISO|never` line per target, `divergences=N`, and `sync retry_after=N` (seconds) while a forge rate limit pause is recorded. Read-only: it never fetches, pushes or recovers.
- `qew accept DIVERGENCE [--reason R] [--completed]`: apply a forge-side state change recorded by the pull. A forge close
  becomes `closed` (reason `not_planned` by default); `--completed` runs the normal completion gate instead and is refused
  without a claim and evidence. A reopen becomes `reopened`; title and label edits apply as field events. DIVERGENCE is the
  divergence event id (a unique prefix) or an item with exactly one open divergence.
  A second effective `claimed` event on a held item (found after a merge) is a divergence too (`DIVERGENCE` is that
  event's id, field `owner`): the holder is the first claim by event order, never by timestamp; `qew accept` hands the item
  to the contender, `qew reject` keeps the holder, each by writing `claim_lost` for the loser. `qew check` counts them
  as a note, `contested claims: N`.
- `qew reject DIVERGENCE`: keep the ledger value; the mirror is marked stale so the next push rewrites the forge.
- `qew import DIRECTORY [--quote Q]`: read a tree written by `qew export` into the ledger, ids kept, as drafts (approved
  with a human's quote, as on create). Mechanical: each `items/ID.md` is parsed header-only (unknown keys, a file name
  that differs from the header id, a set owner, a symbolic link or a non-Markdown file fail and name the file), then
  every id, reference, nesting and cycle is validated against the ledger before the first write (exit 2, nothing
  written). `events.json` is ignored. Not combinable with `--from`, `--forge`, `--repo` or `--history`.
- `qew import [--from TARGET | --forge F --repo URL [--api URL]] [--history]`: read a forge project into the ledger. Issues
  carrying qew's body marker restore their item (id, kind, priority, labels, parent, needs, state from the footer and managed
  labels); any other issue becomes a fresh draft (closed ones arrive closed). Idempotent; records mirrors. `--history` is
  accepted for closed foreign issues.
- `qew restore [--to TARGET | --forge F --repo URL [--api URL]]`: recreate the issues, links and board of a target from the ledger
  alone; mirror files whose remote issue is gone are forgotten first. Idempotent and resumable.
- `qew project`: the group for `ensure`, `status` and `prune`.
- `qew project prune --from OWNER/REPO [--target T]`: delete exactly the cards whose issue is in the named repository
  (case-insensitive, never a prefix) from a board shared by two targets, then re-read; cards left over are an
  `indeterminate` error (exit 1). Refused before any call (exit 2): a source that is not `OWNER/REPO` and the
  target's own repository. Issues are never touched; `removed 0` when nothing matches. Prints `SLUG: removed N`.
- `qew project status [--target T] [--json]`: read-only drift report of each target's board against the ledger: per
  target `<slug>: in sync`, or the lists `missing` (selected items with no card, or no mirror), `stale_column`,
  `stale_priority` and the fix (`qew sync`). JSON rows: `schema_version`, `target`, `in_sync`, `missing`,
  `stale_column`, `stale_priority`, `fix`. Exit 0 in sync (also `boards unsupported`), 1 drifted or the forge failed,
  2 a bad target or no recorded or configured board (names `qew project ensure`). Cards of other repositories are
  ignored. Drift is advisory: forge state is never read back as truth for the ledger.
- `qew project ensure [--target T]`: create or repair the full board where the forge has boards: the seven
  Status options (colors, descriptions), the Priority field P0-P4 and eight views (Kanban, Table, Ready, Blocked,
  Active, Review, Drafts, Roadmap). Idempotent; a corrected view or kept foreign option is printed as a note; extra
  human views are left alone. An overlay mirror (non-empty `select`) prefixes every view filter with
  `label:qew:managed`; repository mode leaves them bare. The board id is recorded in `boards/` in the ledger and
  found by that id even if a human renamed the project. A forge without boards prints `boards unsupported`, exit 0.

## Pull requests
A pull request never carries a closing keyword (any `closes|fixes|resolves` form in the body is refused, exit 2):
finishing work is `qew complete`, or `qew land` after the merge. The ledger records who opened a pull request
and which items it covers (`opener`, `items` in its `pr` mirror); a forge's own text is never that record.
- `qew pr`: the group for `open`, `check`, `ready` and `merge`.
- `qew pr open ITEM [--target T] [--head BRANCH] [--base BRANCH] [--ready] [--what TEXT] [--why TEXT]
  [--testing TEXT] [--body-file FILE] [--json]`: a draft assigned to its opener (the actor, or `QEW_FORGE_LOGIN` when the forge login differs; a login
  the forge refuses leaves it unassigned and prints a note on stderr); the mirror file is written only
  after the forge accepted the open. Approved work needs the caller's claim; a draft item needs none, and `--ready`
  on drafts is refused (also when a covered item is a draft). An epic's pull request covers the epic and its open
  descendants. The body is the marker, `Delivers ID`, then What (default: the item body), Why and Testing, then
  the `--body-file` (a `## Progress` checklist of `- [ ] text <!-- pr-progress:ID -->` lines). Without `--head` the
  pushed branch is derived (git: named, clean, equal to its upstream; jj: the one bookmark equal to origin, git never run).
  A branch named for another item is refused. `--json`: `{item, number, url, draft, schema_version}`.
- `qew pr check NUMBER (ID... | --all | --list) [--target T]`: tick checklist boxes (outside code fences) addressed by marker,
  position, or `@N`; unknown or repeated ids, merged or closed pull requests are exit 2 with no write; a body that
  changed since it was read is exit 3 with no write; the result is read back and a write that did not stick is exit 3.
- `qew pr ready NUMBER [--target T] [--json]`: marks ready, then re-reads; still a draft is exit 4. Already ready
  writes nothing. Refused while a covered item is a draft. `--json`: `{number, ready, changed, schema_version}`.
- `qew pr merge NUMBER [--target T] [--auto] [--admin] [--other-author] [--reason TEXT] [--json]`: refuses, writing
  nothing: held by a covered draft, not open, draft, conflicts or unknown merge state, unticked checklist items (exit 2),
  open child tasks of a claimed covered item, no recorded evidence covering the head (exit 2), failing or pending
  checks (exit 3; unknown check state exit 4). It merges exactly the checked head, re-reads (once more after a second)
  and reports a merge that did not land (exit 3). `--auto` enables forge auto-merge for pending checks, only where the
  base branch requires status checks. `--admin --reason` merges past the check rule only and records the reason on the
  covered items. `--other-author --reason` merges a pull request someone else opened; it overrides ownership only.
  `--json`: `{merged, number, head_sha, items, schema_version}`.
- Ownership: `ready`, `check` and `merge` act on a pull request the caller opened, or whose open covered items the
  caller holds every claim on; otherwise exit 3 naming the opener and the ways forward (`qew start ITEM --take-over
  --reason TEXT`; for merge also `--other-author --reason TEXT`). A merge also refuses an item another actor holds,
  even for the opener. A covered item with unreadable history is exit 4.
- `qew land NUMBER [--target T]`: after the merge, per covered item the caller holds: record the pull request head
  as evidence when none covers it, then complete it (children before epics). Items held by others or not started
  are listed and skipped; a failed gate is reported per item and exits 2.
