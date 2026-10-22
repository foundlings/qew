# Commands

Output is terse and fixed-format: `ID STATE pN kind title [@owner]`, ordered by priority then sortable id
(counter ids numerically) the same way in `ready`, `list` and `blocked`. Titles are flattened, bounded and
quoted or withheld (see security.md). `--json` gives machine output (see Machine output).
An item reference is a full ID, the same ignoring case, or a unique prefix. Exit codes: 0 ok, 1 invalid
ledger or forge failure, 2 refusal or usage, 3 ownership conflict or environment, 4 indeterminate (malformed
data: nothing written), 5 already yours (idempotent, nothing written), 6 claim cap reached.
`--json` (before or after the command) prints `{"schema_version": 1, ...}` for write commands; errors keep the
plain stderr message and the exit code.
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
shape row: id kind labels priority state title | body children_done children_open completable needs owner parent pr schema_version section text_findings tracks work_state holders stale
shape envelope: items schema_version total truncated
shape blocked_row: blocked_by id kind labels priority state title | chain needs owner parent pr root text_findings tracks holders stale
shape edge: item schema_version | needs parent
shape show: author draft id kind labels priority schema_version state title claim holders | approvals blocked_by blocking body children children_done children_drafts children_open comments complete_command completable evidence mirrors needs next owner parent text_findings stale completion closure closed_duplicates supersedes discovered_from discovered
shape triage_row: body id kind labels missing priority schema_version state title | needs owner parent text_findings holders stale
shape check: contradictions cycles problems schema_version text_findings valid
shape inbox_reply: at author excerpt id schema_version section
shape prime: additional_context context_bytes schema_version
```

## Setup
- `qew --version`: print `qew X.Y.Z` (the installed package version); needs no repository, ledger or network.
- `qew init [--ledger PATH]`: write `.qew/config.toml` and the ledger's `qew-ledger.toml` once, at the repository
  root (nearest `.git` or `.jj`; outside one, exit 2), atomically and never through a symlink (exit 2). Prints
  `created PATH` lines plus the trust guidance; a rerun prints `already initialized` and changes nothing. On a jj ledger without
  watchman enabled it also warns on stderr (see `qew doctor`).
- `qew doctor [--fix]`: check the environment. For a jj ledger it reports `ok: watchman is enabled`, or a `warning:` when watchman is not
  installed (`brew install watchman`) or not enabled; jj stats every file of the working copy per snapshot and a ledger is one file per event, so watchman
  cuts a 171,000-file ledger from about 2 s to 0.4 s per commit. `--fix` runs `jj config set --repo fsmonitor.backend watchman` for the ledger's
  repository only (exit 4 when watchman is not installed, exit 2 on a git ledger). A git ledger prints `ok: git ledger, nothing to check`.
- `qew hook install [--claude] [--codex] [--cursor] [--opencode] [--git] [--skip-pre-tool] [--json]`: wire agents (none = all four; `--skip-pre-tool` needs `--claude` alone, exit 2 otherwise, and leaves PreToolUse as it is; output order claude, codex, cursor, opencode, git). `--git` installs the tracked `.githooks/pre-push` and sets `core.hooksPath`; it is never part of the default.
- `qew hook remove [--claude] [--codex] [--cursor] [--opencode] [--git] [--json]`: remove exactly Qew's entries.
- `qew hook session-start --agent NAME`, `qew hook pre-tool --agent NAME`, `qew hook post-tool --agent NAME`:
  the events agents call; see hooks.md.
- `qew hook vcs-check (--pre-push | --revisions REVSET | --message-file FILE)`: refuse pushed commits whose `Refs: ID`
  trailers cite an item without evidence for that commit (exit 2, the exact `qew evidence` command on stderr); see hooks.md.

## Read
- `qew show ITEM [--json]` (also prints `references: owner/repo#123 ...`, the distinct references to other repositories' issues found in the body and comments, as plain text: never followed, never an edge or a path; `references` in `--json`): the first line is the row (`ID STATE pN kind title`), so a draft says draft; then
  header, state, owner, evidence, mirrors, comments (index, author, time, text) and body. JSON adds
  `draft`, and neighbours as `{id, state, title}` objects (`state: missing` for a dangling edge):
  `blocked_by` (unmet needs), `blocking`, `children`, `parent`. An epic also gets `children_open`,
  `children_done`, `children_drafts` and `completable`, a text `progress: 0/3 completed, 2 drafts` line (plus
  `(draft epic)`), `next` (its highest-priority ready child, text `next: ID pN title`) and
  `complete_command` (null unless the epic is claimed with every child completed or closed). `text_findings`
  lists hidden-text findings of the title, body, labels and comments; a title with any is withheld in rows.
  A `tracks:` or `pr:` link prints `KEY: ID URL...`, the linked item's forge URLs read from its `mirrors/` files (header values
  are ledger ids only; `check` flags a URL, a forge number or an unknown id there).
- `qew list [--kind K] [--state S] [--work-state W] [--label L]... [--field NAME[=VALUE]]... [--owner NAME] [--author NAME] [--parent ID]
  [--since START] [--recent [N]] [--limit N] [--json]`: actionable work first; `--since` keeps items created at or after a UTC instant, a date
  (`YYYY-MM-DD`, midnight UTC) or an age back from now (`90m`, `2h`, `3d`, `1w`; anything else is exit 2), and `--recent [N]` (default 100, `0` = all)
  puts the newest N by creation time first (ties by id) instead of priority order, after every other filter, and announces no cut. Every `--label` must match, and every `--field` (an item holding the field `NAME`, or `NAME=VALUE` among its values); labels and names fold ASCII case
  only (a Kelvin-sign lookalike never matches `k`). `--owner` is the current claimant, `--author` the actor
  of the creating event. `work_state` (JSON rows) is the derived class, in list order: `ready`, `blocked`
  (open work with an unmet need, claimed or not: a stuck claim is the more urgent fact), `claimed`, `plan`
  (an epic), `needs_decision` (a draft), `completed`, `closed`; then priority, then id. A filter that
  matches nothing prints nothing and exits 0; `--limit` cuts with a stderr warning. `--bodies` also prints the
  body of each non-draft item (JSON `body`): the explicit inspection; no other list prints bodies.
  A claimed row carries `stale` and a text marker when its recorded pull request merged; `qew check` notes
  `claimed but its pull request merged: IDS (fix: qew land N)`.
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
  of no item (orphans); a child whose `area:` labels share none with its parent's (cross_epic); open work that needs a closed item (closed_needs). A needs chain longer than 30 edges is only
  a warning, reported at its deepest item and counted as depth (exit stays 0). Writes nothing;
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

- `qew export DIRECTORY`: write every item to a new (or empty) directory as `items/ID/item.md` (the ledger's own file
  format) and `events.json` (per item: event type, time, actor; import ignores it). Output is deterministic. A
  non-empty directory, a symbolic link or a ledger with a dangling edge or a cycle is refused before any write.
- `qew export --json`: print the items as one JSON array that `qew create --from-file` accepts (keys `id kind title
  parent needs labels priority created body`), each item after the items it references. Exactly one of
  DIRECTORY and `--json`.

## Work
- `qew create [--kind K] --title T [--key KEY] [--parent ID] [--needs ID...] [--label L] [--field NAME=VALUE]... [--priority N] [--body TEXT]
  [--body-file F | --edit] [--quote Q] [--from-file F [--draft] [--dry-run]] [--json]`: filed into the queue: approved with `filed_by` the creator (Ready, or Backlog while it has unmet needs), or with `--quote` carrying a human's words
  (`"Name: 'their words'"`) recorded as the approval. `--draft` files a draft a human asked for. `--key KEY` (one word, not with `--from-file`) makes a retry safe: the same key and content returns the first item
  with the same output line, the same key under other content is exit 2. `--from-file` takes a plan: JSON Lines, one JSON object or an array (or `-`
  for stdin). The whole plan is validated before the first write (exit 2, naming the line): regular UTF-8
  file, not a symlink; no unknown fields; unique `ref`s; `@ref` entries in `needs`/`parent` resolve in any
  order; no cycle, bad nesting or self edge; `id` (imports keep it), `created` allowed. `--body-file` refuses a symlink (exit 2). An entry with a valid
  `quote` is filed approved with its own words; `--quote` is the default for entries without one; an entry with
  no quote goes straight into the queue; `approved: false` files a draft; `approved: true` needs a quote. `--draft` files every entry as a draft and refuses
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
- Bodies given to `create` and `set` are scanned for commands, flags and paths written as plain prose (`qew start X`,
  `--force`, `/etc/hosts`, `git push`, `gh pr`, `python -m`, `pipx install`). Each distinct token prints
  `warning: unmarked code-shaped text: TOKEN` on stderr, in order of appearance; the write still succeeds. Inline
  code, backtick or tilde fences (an unclosed fence runs to the end) and URLs are not reported.
- `qew set --manifest FILE [--dry-run] [--closed]`: change labels, fields and priorities of many items in one commit. FILE is JSON Lines of
  `{item, add_label?, remove_label?, fields?, priority?, reason?}` (a label or a list of labels; `fields` is `{NAME: value-or-list}`, which replaces those fields and removes one given `[]`; priority 0 to 9). Every row is checked
  before the first write and one bad row refuses all, naming its line (exit 2): an unknown or duplicate item, an unknown field,
  finished work (unless `--closed`), an invalid label or priority, a `gated` label added without a `reason` (kept as a comment `gated: TEXT`; removing the label with a `reason` keeps `gate lifted: TEXT`). A row
  that changes nothing is `unchanged`, so a rerun adds nothing. The writes are the audited `field` events of `qew set`.
- `qew set ITEM... [--title T] [--parent ID] [--needs ID...] [--tracks ID] [--pr ID] [--labels L...] [--field NAME=VALUE]... [--clear-field NAME]... [--priority N] [--due YYYY-MM-DD]
  [--body TEXT | --edit] [--body-file F|-] [--kind K] [--add-label L] [--remove-label L] [--reason TEXT] [--closed] [--json]`: edits the header; adding the label `gated` (work blocked outside the repository: keys, credentials, hardware, an account) needs `--reason TEXT`, kept as a comment `gated: TEXT`, and moves the item to the Gated column, out of `qew ready`; adding the label `needs:approval` (an epic, plan or issue waiting for a human to authorize or accept it) moves the item to the Needs approval column, out of `qew ready`, whatever its claim or pull request, and `--remove-label needs:approval` is the acceptance that returns it to its own column; each change appends a `field` audit event. The whole result is validated
  against the graph before the first write (exit 2, nothing changed): no self edge, unknown or duplicate
  need, `needs` cycle (named), or nesting other than epic > issue > task (a task may also sit under an epic, and an epic under an epic), a parent cycle;
  a kind or parent change must keep existing children valid. Completed or closed work is edited only with
  `--closed`. Several items may share parent, needs, labels or priority; `--title` and `--body` take one item.
  `--field NAME=VALUE` (repeatable) sets a field to those values, replacing it (a repeated NAME is one field with several values), `--clear-field NAME` removes one; the change is one `field` event with `key` `fields`. `--add-label`/`--remove-label` are additive and idempotent (never with `--labels`); `--kind` is revalidated
  against the nesting rules; one `field` event per changed key, and the item is re-read and compared (exit 3
  on a mismatch).
  `qew create` applies the same edge rules before writing.
  No change prints `unchanged` and writes nothing.
  `--edit` runs `$VISUAL`, else `$EDITOR` (split, never via a shell) on an empty temp file in a private
  directory, removed on every path; unset, blank, unparseable, a non-zero exit or an empty result refuse with
  exit 2. `--body-file` refuses a symlink and an empty file.
  Also `[--no-parent] [--kind K] [--add-label L] [--remove-label L] [--body-file F] [--json]`: `--no-parent` removes
  the parent, `--add-label`/`--remove-label` edit the label set (`--labels` replaces it), `--body-file` reads the
  body (a symlink, an empty file, a blank or multi-line title are exit 2). A `needs` change is written as one
  `edge` event per added or removed need, not as a header rewrite, so two branches changing different edges
  merge; other keys rewrite the header with one `field` event each. The files are read back after the write.
  Repeating an edit prints `unchanged`.
- `qew create ... [--draft] [--discovered-from ITEM] [--where T] [--problem T | --goal T] [--fix T | --approach T]
  [--done-when T] [--edit] [--json]`: `--draft` files a draft explicitly (with `--quote` it is exit 2). The structured
  flags render headed sections (`## Where`, `## Problem`, ...); `--problem`/`--goal` and `--fix`/`--approach` are
  exclusive, and `--body`, `--body-file`, `--edit` and the structured flags give the body one way only. `--discovered-from` stores the origin in the header key
  `discovered_from`; `qew show` mentions it at both ends and it never blocks. A negative `--priority` is exit 2; A body, comment or `set --body` with unmarked
  command, flag or path shaped text warns on stderr (`warning: unmarked code-shaped text: X`) and is stored as given.
- `qew block ITEM --on ITEM [--closed] [--json]`, `qew unblock ITEM --from ITEM [--closed] [--json]`: add or remove one
  needs edge as an `edge` event; repeating is `unchanged`; a self edge, unknown item or cycle is exit 2, completed or
  closed items need `--closed`.
- `qew start ITEM [--take-over --reason TEXT | --offline] [--lane ID [--workspace PATH] [--kind compute|token]] [--json]`:
  claim approved work whose needs are completed; prints `ID claimed by OWNER: TITLE` (title cut at 60 characters), so
  no `qew show` is needed to begin. Another actor's claim is exit 3 naming the holder and
  `--take-over`; your own claim is exit 5 (no event) when the lane matches, exit 3 naming the lane when it does
  not; draft, blocked, completed or closed work is exit 2. `--take-over` writes a forced `released` event naming the
  previous owner and a `claimed` event in one command, only for work held by another actor. A take-over moves a claim, so it does not re-check readiness: work that gained an open need after it was claimed changes hands with its needs untouched (blockers gate starting, not handing over).
  A lane (`A-Za-z0-9._-`, 1 to 64, no `--`) tells lanes of one actor apart; `--workspace` (absolute path) and
  `--kind` need `--lane`. The lane guards `start` and `release` only, as good-faith tooling, not access control.
  After the write is merged with the ledger remote, a claim that lost to an older one exits 3 naming the winner.
  `--offline` takes a PROVISIONAL claim without asking the arbiter (primary forge or ledger remote): a `claimed` event
  with `provisional: true`, output and `--json` (`provisional: true`) say so; it cannot combine with `--take-over`
  (exit 2). `[authority] offline_claims = "allow"` makes a plain `start` fall back to the same provisional claim, but
  only when the arbiter is unreachable (or the primary has no issue for the item yet); the default `refuse` keeps
  exit 3 and the message names `--offline`. `qew sync` settles every provisional claim (see ledger.md, Offline claims).
  A task is settled on its nearest mirrored ancestor's issue, as its claim is taken; a claim that cannot be settled yet prints why
  (`the primary has no issue for it yet`, or the forge error). With a primary forge, a claim comment of the caller's own left on the issue while
  the ledger holds no claim is adopted by `qew start`; another actor's refuses (exit 2) and names `qew release ID --force --reason TEXT`.
  `[claims] max_active_claims` in the config caps the caller's active claims (exit 6, take-over included); a
  malformed cap is exit 2 naming the key. `qew show` prints the current claim as `claim: {by, kind, lane, workspace}`.
- Starting an epic claims the epic and its open descendants that can start (drafts, work with unmet needs and finished
  work are left), each through the claim authority, all or nothing: a descendant held by another actor is exit 3 naming
  it and nothing is written; a failure midway leaves no event and releases the reservations already posted on the
  primary forge. Releasing the epic releases the descendants it holds. Evidence on an item claimed by another actor is
  exit 3 unless the caller holds an epic above it; the holder of an epic completes its descendants the same way, offline
  too (completion needs evidence only).
- `qew release ITEM --reason TEXT [--force] [--lane ID] [--json]`: give up a claim; another actor's claim (or another
  lane's) needs `--force`, recorded as `forced` with the `previous_owner`. A lane-scoped claim is released by naming
  its lane (`--lane`); without it the release needs `--force` too. The event records `lane` (the releasing lane) and
  `previous_lane`; the release is re-read and a claim still in force is exit 3. `--json` adds `by` and `lane`.
  Unclaimed or closed work is exit 2, except that `--force` on an item the ledger does not hold clears a claim the primary forge still
  carries (a stale one: no ledger event is written) and says whose it was.
- `qew item`: the group for `attach`, `detach`, `extract`, `rename`, `assign` and `unassign` (the item comes first, then what is done with it, like `qew pr`).
- `qew item attach ITEM SOURCE [--as NAME]`: attach a file, or every file under a directory, to the item (any state). The name
  is `--as` (a directory's files go under it, subdirectories kept) or the file name (a directory's files at the root).
  Names are relative, `/`-separated, without empty, `.` or `..` segments, control characters or backslashes, at most 1024
  bytes (255 per segment); a name cannot be a file and a directory at once. Symlinks and special files are refused.
  Bytes are stored once by hash as `items/ID/attachments/HASH.EXT`; each name is an `attached` event (`path hash size
  blob`); the same bytes under the same name again is `unchanged`. Limits: 100 MiB per file (`QEW_ATTACH_MAX_BYTES`
  overrides), a warning above 25 MiB, 500 MiB of distinct bytes per item. One ledger commit; a refusal writes nothing.
- `qew item attach --manifest FILE [--dry-run]`: bulk attach for importers. FILE is JSON Lines of `{"item", "source",
  optional "name"}` (nothing else; relative sources are relative to the manifest's directory; a source may be a directory).
  Every row is checked first, against the limits, the names already attached and each other; one bad row (named by its
  line) writes nothing anywhere. One ledger commit; the same bytes under the same name are `unchanged`, so a rerun adds
  nothing. `--dry-run` says `would attach` and writes nothing. A create plan entry (`qew create --from-file`) may carry
  `"attachments": [{"source": path, "name": optional}]` (sources relative to the current directory): the files are
  attached to the new item in the same commit, and a bad source refuses the whole plan before any item is written.
- `qew item detach ITEM NAME`: detach a file name, or every name under a directory name, with one `detached` event each; the
  stored bytes stay (history). Nothing attached under the name is exit 2.
- `qew item rename ITEM [NEW] [--after ID] [--dry-run]`: move an item to another id with the `migrate-ids` machinery (item
  file, children and other items citing it, events, mirrors, attachments, named views and local state follow; the old id
  stays in `aliases:` so it still resolves, and a forge issue carrying the old marker is adopted and re-marked on the next
  sync). NEW defaults to the next free id of the item's kind (`qew next-id`); a letters kind accepts only the next letter
  (`--after ID` counts a letter reserved outside the ledger, such as a phase document, as taken). Refused with exit 3 while
  another login holds a claim on it, exit 2 for an unknown item, a taken id or a letter out of order. One ledger commit.
- `qew item assign ITEM LOGIN...` / `qew item unassign ITEM LOGIN...`: further assignees beside the claim owner (which stays
  exactly one), as `assigned` / `unassigned` events naming forge logins (a login already in the wanted state is `unchanged`).
  `qew show` lists them (`assignees:`), sync pushes them with the owner's mapped login (owner first), and the pull turns an
  assignee added or removed on the forge into an event (provenance forge); only the owner's login leaving the issue is a
  divergence.
- `qew item extract ITEM DEST`: write the attached tree to `DEST` (absent or empty) as real files, verifying every hash first.
- `qew comment --manifest FILE [--dry-run]`: bulk comments for importers. FILE is JSON Lines of `{"item", "key", "text",
  optional "author", "at", "reply_to"}`: `key` is required (one word; the same key with the same text is `unchanged`, so a
  rerun adds nothing, with other text it is refused), `author` and `at` (a UTC time) keep who wrote it and when (the event is
  stamped as an import by the importing login), `reply_to` is the `key` of an earlier row or the id of an existing comment.
  Every row is checked first; one bad row (named by its line) writes nothing anywhere; one ledger commit. A create plan entry
  may carry `"comments": [{key, text, author, at, reply_to}]`, added to the new item in the same commit; a bad one refuses the
  whole plan before any item is written.
- `qew comment ITEM (--body TEXT | --body-file F) [--key KEY] [--reply-to COMMENT]`, `qew comment ITEM --edit COMMENT
  (--body TEXT | --body-file F) [--force]`, `qew comment ITEM --delete COMMENT [--force]`: a comment's id is its `commented`
  event id (printed as `commented on ITEM as ID`; a unique prefix works). A reply names its parent and `qew show` threads it
  beneath it; an edit appends `comment_edited` and a delete `comment_deleted` (the original stays in history, the last event by
  id wins, so two machines never write the same file). Only the author edits or deletes; another login needs `--force`, recorded
  as `forced`. `qew check` fails on an edit, delete or reply naming no comment of the item, an unforced change by someone
  else, and a comment linking `attachment:NAME` to a name that is not attached.
- `qew comment ITEM (--body TEXT | --body-file F) [--key KEY]`: append a `commented` event (text kept verbatim, `-` reads
  stdin); exactly one source, non-blank, at most 20000 bytes, a symlinked file refused, else exit 2 before any write.
  Comment text is data and never a state change; unmarked code-shaped text warns on stderr. `--key KEY` (one word) makes a
  retry safe: the same key with the same text posts nothing and prints `(unchanged)`, the same key with other text is exit 2.
- `qew unlock --stale --quote Q [--dry-run] [--json]`: remove the stale git locks (`*.lock` files anywhere inside the ledger's
  `.git`, such as `index.lock` and ref locks) a crashed git left behind, only on a human's quoted approval and only when every
  one is at least 60 s old and open in no process (`lsof`; without it, only when no git process runs). Refuses (exit 3, nothing
  removed) a younger or open lock, and while any qew process holds a ledger lock. Each removal is first appended to the
  ledger's local `.qew/unlock.log` (time, actor, the approving words, each path and age). Never run by qew itself.
  `--stale` is required; no lock is `no stale lock found`, exit 0.
- `qew evidence --manifest FILE [--dry-run]`: record delivery evidence for many items in one commit. FILE is JSON Lines of
  `{item, change_ids: [..]}`. Every row is checked first (exit 2: unknown or duplicate item, a malformed change id, an item that is
  not approved or claimed; exit 3: another login's claim) and a row whose changes are already recorded is `unchanged`. Whether the
  changes landed is `qew complete`'s check, as with the single form. A row may also name a completed item whose header says
  `evidence_absent: historical` (or that has ids): the evidence is recorded and the header gets the ids, replacing the historical
  absence (recovered proof); other stated absences are refused.
- `qew evidence ITEM [--change ID]... [--vcs jj|git] [--on-behalf-of ACTOR]`: record the code changes; default is the current one. Evidence on a claimed item needs the claim (or its epic's). `--on-behalf-of ACTOR` lets another actor (the integrator) record it for a claim ACTOR really holds (the item or its epic; a claim nobody holds, another holder, or oneself is refused, exit 3 / 2): the event records the recording actor and `on_behalf_of`; the claim does not move and `complete` stays the holder's.
- `qew complete --manifest FILE --quote "Name: 'their words'" [--dry-run]`: complete many approved items in one commit on a human's
  quote, without claims or evidence. FILE is JSON Lines of `{item, change_ids?, absent?, reason?}`: strict ledgers need `change_ids` (the work's changes) or `absent` (no-code, historical, children or superseded-by:ITEM, with a `reason`) on every row. Every row is checked before the first write and one
  bad row refuses all, naming its line: an unknown, duplicate, draft or closed item (exit 2), an item claimed by another login
  (exit 3), an open child or an unmet need that the manifest does not also complete (exit 2). An already completed item is
  `unchanged`, so a rerun adds nothing. Each event is `completed` with `bulk_quote` (the words) and the row's `reason`; no parent
  cascade. `qew check` counts these under "completed on a human's quote". The next sync closes mirrored issues as completed.
- `qew complete ITEM [--reason TEXT] [--landed-on BRANCH] [--take-over] [--no-commit WHY | --absent KIND --reason TEXT] [--json]`
  (strict evidence): the cited changes become the item header's `evidence`; with no code to cite, `--no-commit WHY` records the
  absence `no-code: WHY` and `--absent no-code|historical|superseded-by:ITEM --reason TEXT` the stated one (an epic finished by its
  children records `children`). A completion with neither is refused, and so is an item carrying the label `gated` (single and `--manifest`; lift it first with `qew set ITEM --remove-label gated --reason TEXT`); `qew check` reports a completed item that still carries `gated` as an error. Details of the rest of the command: prints
  `ID completed` plus `; ready: ID...` (up to 5 items the completion released; `--json` has `released`). refused (exit 3
  naming the holder) unless the caller holds the claim; an already completed item is a quiet no-op (`completed (unchanged)`, no second event);
  every child must be completed or closed; and (not for epics) an evidence event whose change IDs all landed on the
  target branch (`--landed-on` overrides the configured one for stacked branches) must exist. The `completed` event
  records the `reason`, the full `commits`, the `branch` and the numbers of the item's pull requests recorded as
  merged (`state: merged` in the `pr` mirror), shown by `qew show`. A change this checkout cannot resolve is
  `unknown to this checkout` (exit 2; on jj a divergent change id counts as landed when any of its commits is on the target), a missing target branch is exit 3 (a jj bookmark that two sides moved is conflicted: every side counts as the target), unreadable pull request records are exit 4
  (`indeterminate`, nothing written). Hostile values (a branch with `..` or a leading `-`, a blank reason, an
  evidence id that is not a change id) are exit 2 before any VCS call. `--take-over --reason TEXT` finishes another
  actor's claim after the same proof and records `taken_over_from` (nothing to take over is exit 2).
  `--no-commit WHY` completes without code for work delivered outside the repository, only when no evidence exists
  (exactly one of landed evidence or `--no-commit`); `qew check` counts these as `completed without code: N`.
  An epic needs at least one child and no evidence. When a completion or a close finishes the last open child of
  an epic that has at least one completed child, the epic completes in the same command (`children_finished`) unless someone holds the epic, who completes it; a
  failure of that step is a warning naming `qew complete EPIC` and the command still exits 0.
- `qew close ITEM --reason not_planned|duplicate|superseded [--of ITEM | --by ITEM] [--note TEXT] [--json]`:
  `duplicate` needs `--of` (the original), `superseded` needs `--by` (the replacement), `not_planned` takes neither;
  the linked item must exist and differ from the closed one; `qew show` mentions the link at both ends.
  Finished work is not closed as abandoned: `qew close ITEM --delivered-by SHA --landed-on BRANCH [--json]` records
  work that shipped without a claim as completed under the same landing proof (an `evidence` and a `completed`
  event), so dependents unblock; one flag alone, an unlanded commit or another actor's claim is refused first.
- `qew correct-author --from ACTOR --to ACTOR --quote Q [--dry-run]`: append one `author_corrected` event (`author`, `via` = the old author, the human's `quote`) to every item whose author is `--from`; events are never rewritten, the newest correction wins, a rerun corrects none. `qew show` prints `author: 887 (via qew-import)`, `--json` adds `via`; `qew list --author`, the inbox and the `author:LOGIN` label follow the corrected author, so the next sync relabels and `qew sync --prune-labels` deletes the old label. `qew create --from-file F --import --as ACTOR` credits a new import's events to ACTOR and keeps the actor the file named as their `via`.
- `qew reopen ITEM --reason TEXT [--json]`, or `qew reopen --manifest FILE [--dry-run]`: completed or closed work returns to approved with the reason recorded. The manifest is JSON Lines of `{item, reason}`: every row is checked first (an unknown or duplicate item, or finished work without a reason, refuses all), the `reopened` events land in one commit, and an item that is already open is `unchanged`, so a rerun adds nothing;
  an item that is already open prints `unchanged ITEM (already open)`, exit 0, and writes nothing.

- `qew batch --manifest FILE [--dry-run]`: many mutations in one write session, so one lock, one fetch, one full ledger read, one commit and one push however many rows. The manifest is JSON Lines of `{op, item, ...}`; the ops and their fields are `start` (lane, workspace, kind, offline), `evidence` (change_ids, on_behalf_of), `complete` (reason, absent, no_commit, landed_on, take_over), `close` (reason, of, by, note, delivered_by, landed_on), `reopen` (reason), `comment` (text, key) and `set` (title, parent, priority, kind, add_label, remove_label, body, closed, reason), each as the single command's option of that name. Every row is parsed and its item resolved before anything is written (bad JSON, an unknown op or field, a wrong type or an unknown item refuses the file, naming the line); the rows then run in file order through the single commands' own checks, so a row sees what the row before it did (`start`, `evidence`, `complete` of one item can follow each other). One failing row undoes the whole file (exit as that command would, message `line N: OP ITEM: ...`). Claims made by one file stand or fall together at the ledger remote, as for an epic start. `--dry-run` runs every row and undoes it; it only reads the primary forge (a `start` row is refused when the primary shows another holder, but nothing is posted there). A file that fails withdraws the claims its earlier rows won on the primary, and says which it could not. Rows cannot create items. Prints `applied N rows`;
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
- `qew sync [--target T] [--item ID]... [--all] [--since DATE] [--author NAME] [--owner NAME] [--tree ITEM] [--limit N] [--full] [--dry-run] [--prune-labels]`: pull forge-side changes, then push to every `[[mirror]]` target independently. The pull asks only for what changed (conditional pages or `since`; see forges.md, Incremental pull); `--full` ignores the pull cursor and cached pages and re-reads every issue. A complete listing holding none of two or more mirrored issues means the repository was deleted and recreated: the pull forgets those mirrors (`recreated=N`, no divergences) and the push recreates the issues; one missing issue stays an `exists` divergence, which settles itself (`divergence_rejected`) when the issue is back. The push writes `[replication] forge_concurrency` items at once (default 4; 1 is one at a time): an item starts only after its needs and parent finished, and the first forge failure, a rate limit above all, stops every worker from starting another item (the report says `retry after Ns`), so a rerun finishes the rest (the wait is printed with the UTC time a rerun should work; a GitHub secondary-limit 403 that names no wait is 300s); creates are paced per backend, shared by all workers: GitHub.com defaults to 70 a minute and 450 an hour, every other backend (GitHub Enterprise Server, Forgejo, Gitea, GitLab) to none, and a `[[mirror]]` overrides with `create_per_minute` / `create_per_hour` (0 = no limit); a secondary-limit block teaches the pace: the creates that preceded it become the target's budget for a 30 minute window (kept in the local `.qew/state/TARGET/pace.json`; later runs pace at 80 percent of it, counting the creates of earlier runs, and a clean run that reached that pace raises it by a quarter; a block after fewer than 20 recorded creates teaches nothing, a learned budget expires 6 hours after it was set or raised, and `qew status` shows it as `pace TARGET limit=N per=Ss` with how to reset it); a wait longer than the sync's time budget stops it as `paced` and a rerun continues; an item's attachments are listed in the issue's generated footer (name, size, short hash) as links to `items/ID/attachments/BLOB` on the ledger repository's web view at the ledger branch (the mirror's `ledger_web_url`, else derived from the ledger checkout's `origin`; GitHub `/blob/`, Forgejo `/src/branch/`, GitLab `/-/blob/`), plain names when no address is known; bytes are never uploaded, and the footer is never imported by a pull; comments sync both ways: the push writes each ledger comment to the issue once (a body starting with the marker `<!-- qew-comment:ID -->`, then the text and a footer naming author and time; a reply begins with an `*In reply to @author: ...*` line because forge comments have no parents), updates the same forge comment when the ledger edits it and deletes it when the ledger deletes it; the pull recognises a marked comment as ours (never imported, never pushed twice, adopted by another checkout), imports a human's comment as a `commented` event, and turns a forge edit or removal of any linked comment into `comment_edited` / `comment_deleted` (provenance forge); the link between forge and ledger comment is local sync state, both sides edited between syncs resolve by the later event with the earlier text kept in history, a backend without edit or delete (`update_comment`, `delete_comment`) skips those and says so; the ledger parent of an issue shows as the forge's own sub-issue relation (each call counts against the create pacing; a `[[mirror]]` with `sub_issues = false` turns the mapping off) (GitHub: the child is added to the parent's sub-issues, replacing a previous parent, or removed when the ledger parent goes), set once and again only when the ledger parent moves; a sub-issue call the forge refuses with 422 is read back: when the forge already shows the ledger parent, the parent is recorded and not retried, otherwise the refusal is a failure; a wait of more than a minute for the create window is announced on stderr with its length and end time; a parent changed on the forge is a `parent` divergence (never silently overridden): `qew accept` makes the item mirrored as that issue the ledger parent, `qew reject` puts the ledger parent back on the next push; the pull imports the reviews and inline review comments of every pull request the ledger tracks (its `pr` mirror files) as `commented` events on the first item the pull request covers (author, original time, provenance forge; text `Review STATE`, or `Review comment on path:line`, then the body; stored once by remote reference); an open pull request is read on every pull, a merged or closed one to the end once and then left alone (`reviews_read`); backends without review reads skip it and say so; the reaction counts of an issue (the summary already in its payload) are kept in the local mirror state and shown by `qew show` (`reactions: +1 3, heart 1`, `reactions` in `--json`); they are display only: no event, no divergence, nothing pushed; an issue first imported from the forge also records its activity history once, as `forge_timeline` events (`kind`, `who`, `when`, optional `detail`; history only, no state effect, at most 200 per issue) which `qew show` lists under `timeline:`; `timeline = false` on a `[[mirror]]` turns it off, and backends without a timeline skip it; the same import records the earlier wordings of the issue body (GraphQL `userContentEdits`, oldest first) as `body_revision` events (`who`, `when`, `hash` of the whole text, `text` up to 4000 characters; history only, never mirrored back; the current body stays the item body); `body_history = false` on a `[[mirror]]` turns it off, and backends without it skip it; a mirror naming `issue_types = { p1 = "Bug", issue = "Task", epic = "Feature" }` (GitHub; keys are `pN` for a priority, tried first, then the item kind) sets each issue's type from that map on create and on every change that alters the mapped name, and a type changed on the forge is a `type` divergence (accept keeps the forge type until the ledger side changes; reject restores the mapped one); without the option no type is sent or compared; a mirror with a board and `project_fields = { Area = "label:area", Kind = "kind" }` (GitHub; sources `kind`, `priority`, `state`, `label:PREFIX`) sets those custom card fields (text, or single-select by option name) when the mapped fact changes, remembers what it set, and reports a card value changed in the Projects UI as a `project:NAME` divergence (reject sets it again on the next sync); a source with no value sets nothing; a field the project lacks is a failure naming it; a 5xx or lost connection on a call that is safe to repeat (reads, updates, deletes, GraphQL queries, a few idempotent mutations, sub-issue calls; never a create) is retried with exponential backoff and jitter, honouring `Retry-After`: `[[mirror]] forge_retries` (default 4, 0 = none, at most 20) and `forge_retry_max_wait` (seconds, default 60), and the summary says how many calls were retried; an item that still fails is listed as failed and the sync goes on with the others, and `[replication] forge_failure_limit` (default 5) consecutive failed items stop the target (a rate limit with a named wait still stops it at once); a rerun continues; an issue with an open divergence (not a contested claim) is marked with the label `qew:diverged`, taken off the project board (its issue stays, and an update leaves its open or closed state alone) and, on an overlay (managed) mirror, loses `qew:managed` so views filtered on it hide it; settling the divergence returns all three on the next sync; a forge-side change the ledger did not make (a divergence) is settled by the same sync under the default `[replication] divergences = "ledger"`: the ledger value is kept, the push restores it on the forge, and `divergence_rejected` is recorded with the reason `auto: the ledger is the source of truth` (contested claims keep their arbitration); `"report"` leaves divergences for `qew accept` / `qew reject`; a create that was applied but not recorded is adopted by its marker, never duplicated. `--dry-run` reads the forge listing and writes nothing, so it lists `create` lines for a wiped repository (an unreadable forge is noted and the plan comes from the ledger only). On a target with a
  board each card's Status (the nine-status model) and Priority (P0-P4; larger priorities clamp to P4 with a
  warning) are set, read back from the forge a bounded number of times, and recorded only once visible (else the item
  is reported failed and retried next sync). A card moved by hand is moved back. Items that left the target's `select`
  lose their card (`pruned N`); their issues are never closed. `--item ID` (repeatable; the pass the background worker runs after
  writes) syncs only those items: it reads their issues and cards one by one against the recorded board, looks for an
  existing issue only among the newest page, and skips the full pull, milestones and prune. It also refreshes the mirrored items whose
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
  Scope: `--since` (an event at or after a UTC instant or date), `--author`, `--owner`, `--tree ITEM` (the item and everything below it) and `--item` narrow the run to the items all of them agree on, and take the targeted pass (the named issues are read one by one: no whole-board listing, no pruning, no milestone sync). `--limit N` writes at most N pending items, parents first, and a rerun continues. An unscoped sync, or a scoped one without `--limit`, whose pending writes exceed `[replication] sync_limit` (default 300; 0 lifts it) is refused with exit 2, the count and the ways out; `--all` writes it anyway, a bare `--item` list and `--dry-run` are never refused (a dry run notes the excess). A scope that matches nothing says `no item matches the scope`. The background worker that runs after writes syncs its queued items and logs the refusal for a catch-up that is too big. Pending counts come from the ledger alone: a wiped repository's recreated issues are not counted in advance.

  `qew sync --prune-labels [--target T] [--dry-run]` does not sync: it deletes, per target, the label definitions qew itself retired and
  nothing else. Retired is a label `PREFIX:VALUE` whose prefix is a field name in use on an item or mapped by the mirror's `project_fields`
  (`phase:12` once `phase` is a field), or a label in one of qew's own namespaces (`qew:` `kind:` `priority:` `status:` `author:`) that no item
  produces any more (`author:old` after `qew correct-author`; `qew:managed` in repository mode). A label that merely nothing carries is never
  deleted (forge defaults, a team's labels, labels made in preparation), nor are the labels qew keeps emitting (`epic` `task` `P0`..`P4` `ready`
  `in-progress` `gated` `needs:approval`), nor any label an issue or pull request (open or closed) still carries. It prints
  `TARGET: deleted label NAME` (`would delete` with `--dry-run`, which reads the forge but deletes nothing) or `TARGET: no retired labels`. It
  refuses a read-only target (exit 2) before any call, and takes neither `--item` nor `--full`.
- `qew mirror add FORGE REPO [--read-only] [--api URL]`, `qew mirror remove TARGET`, `qew mirror list [--json]`: edit the
  `[[mirror]]` entries of `.qew/config.toml` line by line; comments and every other table stay byte for byte. `add` refuses
  an unknown forge, an invalid URL and a repeated target (exit 2). `remove` refuses the primary forge (exit 2) and keeps
  the target's mirror files. A target is one-way (`read-only`) when marked `read_only = true` or when a primary is set
  and it is not the primary: pull never imports its content edits, new issues or comments, and only flags a close or
  reopen as a divergence. `list` prints `SLUG primary|two-way|read-only FORGE REPO`.
- `qew mirror rehome ITEM --from TARGET`: move an item that was filed as an issue in a repository that is not this ledger's.
  It comments a pointer on that issue, closes it (`not_planned`, read back; exit 4 if still open), and forgets the item's
  mirror record for TARGET, so the next `qew sync` files it in the configured targets. TARGET need not be configured (the
  forge and repository come from the record); a configured target, or no matching issue record, is exit 2.
- Mirror targets come from the `.qew/config.toml` of the repository the command runs in. With `QEW_LEDGER` naming a
  ledger other than the one that config names, a config that has mirror targets is refused (exit 2) rather than used.
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
  are the nine statuses of `sync_plan.column`, stacked top to bottom (`Draft (2)` then one card line per card:
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
- `qew pull [--json]`: fetch and merge the ledger remote, and nothing else: no forge call, no sync worker, no claim arbiter, whatever `fetch_interval_s` says. Prints `up to date`, or `pulled N commits; M items changed: IDS` (JSON: `commits`, `items`, `from`, `to`). Exit 3 when the remote is unreachable or offline mode is on, and the merge conflict error when the ledger diverged (settle it with `qew resolve ledger`, then `qew sync`). It works with `auto_push = false`. A command that finds no item or pull request (`no item matches`, `pull request #N is not recorded`) pulls once the same way and looks again before it refuses, unless `QEW_OFFLINE`, `QEW_NO_FETCH`, `auto_push = false`, or it has already written ledger files.
- `qew status [--json]`: `ledger uncommitted=N unpushed=N mode=online|offline remote=ok|offline|none`, one `target SLUG pending=N [unbaselined=N] synced=ISO|never repos=on|off pulled=ISO|never next_pull=conditional|incremental|full full_set=ISO|never` line per target (`unbaselined=N`: mirrored items with no committed baseline (a ledger last synced by a qew older than 6.31); the first `qew sync` records it from the forge and writes nothing, and `sync_limit` does not count them; JSON: `unbaselined`; `repos=off`: no git_url/ledger_git_url, so sync does not push the repositories; JSON: `last_pull`, `next_pull`, `last_full`), `divergences=N`, a `claim ITEM provisional owner=X` line per unsettled offline claim and a `claim ITEM lost winner=Y` line per dropped one (JSON: `claims.provisional`, `claims.lost`), and `sync retry_after=N` (seconds) while a forge rate limit pause is recorded. Read-only: it never fetches, pushes or recovers.
- `qew accept DIVERGENCE [--reason R] [--completed]`: apply a forge-side state change recorded by the pull. A forge close
  becomes `closed` (reason `not_planned` by default, or the reason the forge gave: `duplicate`, or completed work); `--completed` runs the normal completion gate instead and is refused
  without a claim and evidence. A reopen becomes `reopened`; title and label edits apply as field events. DIVERGENCE is the
  divergence event id (a unique prefix) or an item with exactly one open divergence.
  A second effective `claimed` event on a held item (found after a merge) is a divergence too (`DIVERGENCE` is that
  event's id, field `owner`): the holder is the first claim by event order, never by timestamp; `qew accept` hands the item
  to the contender, `qew reject` keeps the holder, each by writing `claim_lost` for the loser. `qew check` counts them
  as a note, `contested claims: N`.
- `qew reject DIVERGENCE`: keep the ledger value; the mirror is marked stale so the next push rewrites the forge.
- `qew conflicts [--json]`: every open sync conflict, ledger merge conflict first. Kinds: `ledger_merge` (two checkouts
  changed the same ledger file; the last fetch recorded it in the local `.qew/merge-conflict.json`, and every command
  that fetches keeps failing with exit 1 until it is settled), `divergence` (the forge changed something the ledger did not)
  and `claim_contest` (two effective claims). Each entry gives `kind`, `id` (the reference `qew resolve` takes), `item`,
  `files` and per-key `differences` (`ours`/`theirs`) for a merge, `forge` and `ledger` values for the others, `resolvable`
  and the exact `resolve` commands. `--json` is `{schema_version, conflicts: [...]}`; no conflicts prints `no conflicts`
  (exit 0). Read-only and offline: it never fetches. `qew status` prints `conflicts=N` and `qew prime` / the session-start
  hook a `Conflicts:` line pointing here when N > 0.
- `qew resolve REF --take SIDE --reason TEXT`: settle one conflict; `--reason` is required and recorded. `REF` is `ledger`
  (the merge conflict: `--take ours` keeps this checkout's files, `--take theirs` the remote's; exit 2 if none is pending)
  or a divergence event id / an item with one open divergence (`--take forge` = `qew accept`, `--take ledger` = `qew reject`;
  a claim contest hands the item to the contender or keeps the holder). A merge is settled whole-file, only for ledger-owned
  files (`items events mirrors boards views proposals`); a conflict in any other file exits 2 and nothing is touched. The
  side not kept is written to a `conflict_resolved` event (`took`, `reason`, `discarded`: path to text, bounded) on the
  item and stays in the merge commit's other parent. A divergence's `divergence_accepted`/`divergence_rejected` event
  carries the same `reason`. `qew accept --completed` stays the way to complete on a forge close.
- `qew import DIRECTORY [--quote Q]`: read a tree written by `qew export` into the ledger, ids kept, as drafts (approved
  with a human's quote, as on create). Mechanical: each `items/ID/item.md` is parsed header-only (unknown keys, a directory name
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
- `qew project prune (--from OWNER/REPO | --orphans) [--target T]`: with `--orphans`, delete the cards of the target's own repository whose issue no ledger item mirrors (a stray issue closed or moved by `qew mirror rehome` keeps its card); exactly one of the two is required (exit 2). With `--from`, delete exactly the cards whose issue is in the named repository
  (case-insensitive, never a prefix) from a board shared by two targets, then re-read; cards left over are an
  `indeterminate` error (exit 1). Refused before any call (exit 2): a source that is not `OWNER/REPO` and the
  target's own repository. Issues are never touched; `removed 0` when nothing matches. Prints `SLUG: removed N`.
- `qew project status [--target T] [--json]`: read-only drift report of each target's board against the ledger: per
  target `<slug>: in sync`, or the lists `missing` (selected items with no card, or no mirror), `stale_column`,
  `stale_priority`, `orphan` (`#N` cards of the board's own repository that no item mirrors) and the fix (`qew sync`, and `qew project prune --orphans` for orphans). JSON rows: `schema_version`, `target`, `in_sync`, `missing`,
  `stale_column`, `stale_priority`, `orphan`, `fix`. Exit 0 in sync (also `boards unsupported`), 1 drifted or the forge failed,
  2 a bad target or no recorded or configured board (names `qew project ensure`). Cards of other repositories are
  ignored. Drift is advisory: forge state is never read back as truth for the ledger.
- `qew project ensure [--target T]`: create or repair the full board where the forge has boards: the nine
  Status options (colors, descriptions), the Priority field P0-P4 and eight views (Kanban, Table, Ready, Blocked,
  Active, Review, Drafts, Roadmap). Idempotent; a corrected view or kept foreign option is printed as a note; extra
  human views are left alone. An overlay mirror (non-empty `select`) prefixes every view filter with
  `label:qew:managed`; repository mode leaves them bare. The board id is recorded in `boards/` in the ledger and
  found by that id even if a human renamed the project. A forge without boards prints `boards unsupported`, exit 0.

## Pull requests
A pull request never carries a closing keyword (any `closes|fixes|resolves` form in the body is refused, exit 2):
finishing work is `qew complete`, or `qew land` after the merge. The ledger records who opened a pull request
and which items it covers (`opener`, `items` in its `pr` mirror); a forge's own text is never that record.
- `qew pr`: the group for `open`, `check`, `ready`, `close` and `merge`.
- `qew pr open` for an item no mirror selects (a task when the mirror takes epics and issues): the pull request is recorded on the
  item, and the issue it references and the target come from the nearest mirrored ancestor, as task claims do; ready, check and
  merge then work as for any other item. An item the mirror does select but has not synced still asks for `qew sync`.
- `qew pr open ITEM [--target T] [--head BRANCH] [--base BRANCH] [--ready] [--what TEXT] [--why TEXT]
  [--testing TEXT] [--body-file FILE] [--adopt-legacy NUMBER --reason TEXT] [--json]`: a draft assigned to its opener (the forge login: `QEW_FORGE_LOGIN`, else the mirror's `assignees` entry for the actor, `[forge.logins]`, `[forge] login`, else the actor itself; a login
  the forge refuses leaves it unassigned and prints a note on stderr); the mirror file is written only
  after the forge accepted the open. Approved work needs the caller's claim; a draft item needs none, and `--ready`
  on drafts is refused (also when a covered item is a draft). An epic's pull request covers the epic and its open
  descendants. The body is the marker, `Delivers ID`, then What (default: the item body), Why and Testing, then
  the `--body-file` (a `## Progress` checklist of `- [ ] text <!-- pr-progress:ID -->` lines). Without `--head` the
  pushed branch is derived (git: named, clean, equal to its upstream; jj: the one bookmark equal to origin, git never run).
  A branch named for another item is refused. `--json`: `{item, number, url, draft, schema_version}`.
  An open pull request already on the head whose body carries this item's marker (an orphan: the forge accepted the
  open and the ledger record was never written) is adopted instead of opened again: only the record is written and the
  result ends with `adopted`. Any other open pull request on the head (no marker, another item's, a different base,
  several) is refused with exit 2 naming it, never taken over. The owner's explicit exception is `--adopt-legacy NUMBER --reason TEXT`:
  the open, unrecorded, unmarked pull request NUMBER on the head (one from before qew) keeps its number and head, gets this item's
  marker line, is recorded and the reason is kept as a comment on the item; a merged, closed, recorded or marked one is refused.
  A forge that cannot list by head opens as before.
- `qew pr check NUMBER (ID... | --all | --list) [--on-behalf-of ACTOR] [--target T]`: tick checklist boxes (outside code fences) addressed by marker,
  position, or `@N`; unknown or repeated ids, merged or closed pull requests are exit 2 with no write; a body that
  changed since it was read is exit 3 with no write; the result is read back and a write that did not stick is exit 3.
  `qew pr check NUMBER --drop ID --reason TEXT [--force]` removes that item's box (the pull request will not finish it) and nothing else: every other byte and tick stays, the read-back rule is the same, and the drop with its reason is kept as a comment on each covered item and in the pull request record (`dropped`); the item stays open in the queue. A ticked box is refused (exit 2) unless `--force`; no `--reason`, an unknown id, or `--drop` combined with ticking, `--all` or `--list` is exit 2.
- `qew pr check NUMBER --add ITEM [--add ITEM]...` adds an unticked `- [ ] TITLE <!-- pr-progress:ITEM -->` entry per item after the last box (or in a new `## Progress` section at the end of a body with no checklist) and changes no other byte; it stands alone (not with ticking, `--all`, `--list` or `--drop`). The pull request must be recorded and yours (the ownership rule of `ready`) and open; an unknown or repeated item, an item whose marker is already in the body, and a title carrying a closing reference or a comment marker are exit 2; a body that changed since it was read is exit 3 with nothing written. Each addition is kept as a comment on the covered items. The new entry ticks like any other, and the item joins the pull request record's covered items, so the merge gates see it.
- `qew pr ready NUMBER [--on-behalf-of ACTOR] [--target T] [--json]`: marks ready, then re-reads; still a draft is exit 4. Already ready
  writes nothing. Refused while a covered item is a draft. `--json`: `{number, ready, changed, schema_version}`.
- `qew pr body NUMBER --body-file FILE [--on-behalf-of ACTOR] [--target T] [--json]`: replaces a recorded, open pull request's body with the file's text (the ownership rule of `ready`; `--on-behalf-of ACTOR`, accepted by every recorded-pull-request verb except `open` and `merge`, judges that rule for ACTOR, so a coordinator acts where the opener or claim holder could and naming someone who could not is exit 3), for a stale body no other command rewrites. Every `pr-progress` box of the current body must survive in the file (a missing one is exit 2 naming it; `pr check --drop` is the way to remove an item), no box may be ticked there that is not ticked now (exit 2: only `pr check` ticks), and the ticks already earned are carried into the new text. An empty file, a closing reference (`Fixes #N`) and a merged or closed pull request are exit 2; a body that changed since it was read is exit 3 with no write; the result is read back. The same text prints `(unchanged)` and writes nothing. `--json`: `{number, changed, schema_version}`.
- `qew pr draft NUMBER [--on-behalf-of ACTOR] [--target T] [--json]`: the inverse of `ready`: moves a recorded, open pull request back to draft (the ownership rule of `ready`), reads it back (still ready is exit 4) and records `draft` in the pull request record. Already a draft writes nothing and prints `(unchanged)`; a merged or closed pull request is exit 2. No covered item's state changes. `--json`: `{number, draft, changed, schema_version}`. GitHub converts it with `convertPullRequestToDraft`; GitLab prefixes `Draft:` and Forgejo `WIP:` to the title.
- `qew pr label NUMBER [--add L]... [--remove L]... [--on-behalf-of ACTOR] [--target T] [--json]`: adds and removes labels on a recorded pull request (the ownership rule of `ready`; any state; `--on-behalf-of ACTOR` judges that rule for ACTOR, so a coordinator can label what the opener or claim holder could, and names someone who could not is exit 3), creating a missing label and keeping every label it was not told about, then reads the pull request back; success is printed only when the forge shows the wanted set (accepted but not shown: exit 4). A rerun that changes nothing prints `(unchanged)` and writes nothing. No labels asked, a label both added and removed, or a malformed name (1 to 50 printable characters, no comma) is exit 2. `--json`: `{number, changed, labels, schema_version}`. GitHub only (other backends raise `Unsupported`).
- `qew pr merge NUMBER --check [--auto|--admin|--other-author --reason TEXT] [--json]` runs every refusal the merge would apply (ownership and readable history, draft hold, finished, draft, conflicts, unticked boxes, open children, evidence covering the head, checks) and stops: it reads the ledger and the forge and writes neither. With none, it prints `pr #N: qew merge preflight passed (ledger and forge refusals only); nothing was merged` (it speaks for qew's own refusals, never for a caller's gate) and exits 0; otherwise it exits with the first refusal's code, that message on stderr (the merge's own text) and the rest after it. `--json` prints `{mergeable, refusals: [{message, exit_code}]}` and exits the same way. A refusal the merge reaches only by running out of options (`--auto` on a base that requires no checks) is not predicted.
- `qew pr sync NUMBER --items [--on-behalf-of ACTOR] [--target T]` re-derives the record's covered items as the recorded ones plus the root's open descendants plus every item with a Progress box (items whose box was dropped are not re-added), keeps a comment on each covered item and prints `unchanged` when nothing joined; it only adds, never removes, and repairs a record that froze before its children existed. A merged or closed pull request is refused (exit 2).
- `qew pr sync NUMBER [--on-behalf-of ACTOR] [--target T] [--json]`: sets an open pull request's title to the current title of the item it was opened for (the ownership rule of `ready`), reads it back, and records `pull request #N retitled: OLD -> NEW` as a comment on the item; a title that already matches prints `unchanged` and writes nothing; a merged or closed pull request is refused (exit 2). The body is not touched: it is composed once at open from `--what/--why/--testing` and carries the checklist's ticks, so regenerating it would erase them. GitHub only (other backends raise `Unsupported`).
- `qew pr close NUMBER [--on-behalf-of ACTOR] [--target T] [--item ID --reason TEXT] [--json]`: closes a recorded, unmerged pull request (same ownership rule as ready). `--item ID --reason TEXT` is the way for an unmerged pull request qew never recorded (a legacy draft from before qew): it adopts the pull request onto that item (which carries its remainder), closes it, read back, writes its `pr` mirror as closed and keeps the reason as a comment on the item; a merged one is refused (exit 2), `--item` on a pull request recorded for another item is refused, and a rerun is unchanged.
  re-reads, and records `closed` in its mirror; still open is exit 4, merged is exit 2, already closed writes nothing.
  An orphan is adopted with `qew pr open` first. `--json`: `{number, closed, changed, schema_version}`.
- `qew pr merge NUMBER [--target T] [--auto] [--admin] [--other-author] [--reason TEXT] [--json]`: refuses, writing
  nothing: held by a covered draft, not open, draft, conflicts or unknown merge state, unticked checklist items (exit 2),
  open child tasks of a claimed covered item, no recorded evidence covering the head (exit 2), failing or pending
  checks (exit 3; unknown check state exit 4). It merges exactly the checked head, re-reads (once more after a second)
  and reports a merge that did not land (exit 3). `--auto` enables forge auto-merge for pending checks, only where the
  base branch requires status checks. `--admin --reason` merges past the check rule only and records the reason on the
  covered items. `--other-author --reason` merges a pull request someone else opened; it overrides ownership only.
  `--json`: `{merged, number, head_sha, items, schema_version}`. After a landed merge the head branch is deleted on the
  forge, never the base, the configured target branch or a head another open pull request uses (`branch NAME kept: ...`);
  a forge that cannot delete only warns on stderr and the merge still succeeds.
- `qew next-id [--kind epic|issue|task] [--count N] [--after ID]`: print the next free id (or N consecutive ids) of the kind's
  `id_mode`, one per line; nothing is written. Letters run in strict order (A..Z, AA..ZZ, AAA.., after ZZZ comes AAAA):
  the next letter follows the end of the main run of letter ids and ignores far-off outliers (a gap of more than three
  blocks of two-letter ids ends the run, so PID and PIE after ADF do not move it); an id that is taken is skipped.
  `--after ID` (letters kinds) counts a letter reserved outside the ledger, such as a phase document, as taken. A plan
  that names a letter id for a letters kind outside `--import` may not jump far past the end of the sequence (more than three
  blocks of two-letter ids), else exit 2 naming the next letter: hand-named ids like PIE after ADF are refused.
- `qew migrate-layout [--dry-run]`: move a ledger of any older schema straight to the latest (7) in one commit; schema 7 declares the committed `baselines/` layout and changes no file but the `schema_version` line (the first whole-target `qew sync` writes the baselines; a qew older than 6.31 refuses the ledger); schema 6 sets `core.untrackedCache` in the git config of a ledger that has a `.git` (a git ledger or a colocated jj one; a hand-run `git status` of foundlings-ledger takes 0.5 s, not 2 s; jj's own snapshot, about 1.1 s, is unaffected) and changes no file but the `schema_version` line; schema 5 adds the item `fields` header key (an existing item file is unchanged, so 4 to 5 only moves the `schema_version` line); schema 4 adds the evidence header: every completed item gets `evidence` from the ids its events cite that still resolve and landed on the target (kept as cited where there is no code repository here), else the typed absence its completion recorded (`no-code`, `children`, or `historical` with the importer's or bulk path's reason). Before that, for schema 1 or 2: each `items/ID.md` becomes `items/ID/item.md` with identical bytes, and the `schema_version` line becomes 3. It refuses (exit 1) when an item already has both paths, and any failure rolls the whole step back. Only a ledger older than 4 waits for other logins' claims (it moves files); 4 to 5 changes the `schema_version` line alone and keeps every claim. The claim check (exit 3, another login holds a claim) and `qew check` run on the result. A ledger already at the latest prints that and changes nothing. Every other command on an older ledger refuses (exit 2) naming this command; a qew older than the layout refuses schema 3. Run it with all writers pushed and quiet: an unpushed edit to an item file in another clone conflicts with the move.
- `qew migrate-ids --to base35-time [--dry-run]`: rename every item whose id is not a base35-time id (letters, counter, imported) to a
  new base35-time id, ordered by creation, and carry every reference: item headers (`parent needs tracks pr discovered_from`),
  event directories and the ids cited in `edge`, `closed` and link `field` events, mirror directories and pull request
  mirrors' `items`, named board views' `epic:` and `parent:` terms. Event ids, history and forge numbers are never
  changed. The old id becomes an `aliases:` header value (space separated item ids; an alias may not be an item's own id,
  another live item's id or another item's alias): `show`, `set` and every item argument resolve an alias, and sync, pull
  and import treat an issue whose body still carries an old marker as the same item (adopted, never duplicated; the next
  sync rewrites the marker). One write session, one ledger commit. Refused (exit 3, nothing written) while any item is
  claimed by another login. Idempotent: a finished ledger prints `nothing to migrate`; a run that stopped midway leaves a
  ledger `qew check` accepts (aliases resolve what is not yet rewritten) and the rerun finishes it. `--dry-run` lists the
  items (`would migrate ID`) and writes nothing. `--to` accepts only `base35-time`.
- `qew repair-evidence [--dry-run]`: rebuild the missing evidence header of completed items from their events, with the schema 4
  migration's rule (ids that did not visibly fail to land stay as cited, else the typed absence `historical`), in one commit; an
  imported history entry (`qew create --from-file --import`) gets its header the same way at import. Prints `nothing to repair`
  when every header is there.
- `qew repair-closes [--dry-run]`: restore items an older pull recorded as `closed not_planned` (provenance `forge-import`)
  although the forge's first close of the issue (its event timeline, not the current reason a push may have rewritten) was
  completed or gave no reason: appends `reopened` and `completed` (both `forge-import`), then syncs those items so the forge
  shows completed again. A first close of `not_planned` or `duplicate` is left alone; a forge without close history is
  skipped. Idempotent: `nothing to repair`. `--dry-run` lists the items and writes nothing.
- Close reasons on the forge: a pulled closed issue keeps the forge's reason (`completed` or none: completed work,
  `not_planned`, `duplicate`); sync never sends `not_planned` for anything else. Ledger `duplicate` and `superseded` both
  reach GitHub as `duplicate` (GitHub has no superseded reason). `qew accept` of a forge-side close follows the reason the
  forge gave unless `--reason` names one.
- `qew branch`: the group for `prune`.
- `qew branch prune [--target T] [--base BRANCH] [--dry-run]`: per forge target (read-only targets skipped), delete every
  branch whose tip `BRANCH` (default the configured target branch) already holds and that no open pull request uses.
  The base, the configured target branch, the forge's default branch and protected branches are never touched. Deleting
  needs no flag and prints each name; `--dry-run` prints `would delete NAME` and deletes nothing. A forge that cannot
  list or delete (GitLab) prints `branches unsupported`, exit 0; any other forge failure is exit 1 and a branch whose
  merged state could not be read is never deleted.
- Ownership: `ready`, `check` and `merge` act on a pull request the caller opened, or whose open covered items the
  caller holds every claim on; otherwise exit 3 naming the opener and the ways forward (`qew start ITEM --take-over
  --reason TEXT`; for merge also `--other-author --reason TEXT`). A merge also refuses an item another actor holds,
  even for the opener. A covered item with unreadable history is exit 4.
- `qew land NUMBER [--target T] [--integrator --reason TEXT] [--json]`: after the merge, per covered item the caller holds: record the pull request head
  as evidence when none covers it, then complete it (children before epics). Items named only by a Progress box of the merged body (the
  record froze at open) are covered too, but only when their own evidence is the merged head or an ancestor of it; without that the line
  says `has a Progress box in pr #N but no evidence on its merged head` and names `qew evidence`, because the head is no proof for them. Items held by others or not started
  are listed and skipped (an item another actor holds is reported with the exact `qew complete ITEM --take-over`
  command, never touched); a failed gate is reported per item and exits 2. A pull request merged into a branch other
  than the configured target leaves its items `still open`; a merge head that is not a commit id is exit 4 and writes
  nothing; a run that landed nothing prints `nothing to land` (`nothing to land: all covered items complete` when every covered item is already finished; exit 0). `land` does not apply the opener rule of the other `pr` commands: it checks the holder of each item itself, so any actor may run it and only items it holds (or `--integrator` names) are touched. `--json`: `{state, landed, held: [{id, holder}], clean,
  open, schema_version}`. `--integrator --reason TEXT` is for the one who merged: it also lands the items another actor
  holds, on the `complete --take-over` path (evidence recorded for the holder, the completion event carrying the holder
  and `integrator landed pr #N (held by HOLDER): TEXT`); `--reason` alone, or `--integrator` without it, is exit 2, and an
  unmerged pull request is still refused. It never trusts the forge's `merged_by`: a shared token would make that match everyone.
