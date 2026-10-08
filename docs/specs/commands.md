# Commands

Output is terse and fixed-format: `ID STATE pN kind title [@owner]`, ordered by priority then sortable id
(counter ids numerically) the same way in `ready`, `list` and `blocked`. Titles are flattened, bounded and
quoted or withheld (see security.md). `--json` gives machine output (see Machine output).
An item reference is a full ID, the same ignoring case, or a unique prefix. Exit codes: 0 ok, 1 invalid
ledger or forge failure, 2 refusal or usage, 3 ownership conflict or environment, 4 indeterminate (malformed
data: nothing written), 5 already yours (idempotent, nothing written), 6 claim cap reached.
`--json` (before or after the command) prints `{"schema_version": 1, ...}` for write commands; errors keep the
plain stderr message and the exit code.

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
shape row: id kind labels priority state title | needs owner parent schema_version text_findings holders stale
shape envelope: items schema_version total truncated
shape blocked_row: blocked_by id kind labels priority state title | chain needs owner parent root text_findings holders stale
shape edge: item schema_version | needs parent
shape show: id kind labels priority schema_version state title claim holders | blocks body children evidence mirrors needs owner parent text_findings stale author completion closure closed_duplicates supersedes discovered_from discovered comments
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
- `qew list [--kind K] [--state S] [--label L] [--json]` (a claimed row carries `stale` and a text marker when its pull request merged; `qew check` notes `claimed but its pull request merged: IDS (fix: qew land N)`), `qew ready [--json]`, `qew blocked [--chains] [--json]`,
  `qew graph [--format text|dot|mermaid] [--json]`: dependency edges (`A -> B`, A needs B) then parent edges
  (`A parent P`; dashed in dot and mermaid), sorted by id; labels are escaped for their syntax, a title hiding
  characters is withheld; `--json` and `--format` are exclusive. Readiness is derived from files; no network. `ready` lists by priority, then the
  longest chain it unblocks, then direct dependents, then id. `blocked --chains` adds, under each row,
  `  chain: ITEM -> NEED -> ... [root STATE]`: the longest chain of unmet needs (ties: lowest id) to a root blocker
  that has no unmet needs, is closed, or is `dangling` (no such item); `--json` rows gain `chain` and `root`.
  A needs cycle is refused (exit 1).
- `qew check`: schema, duplicate IDs, cycles, dangling needs, illegal transitions, completed without
  evidence, malformed mirrors. Writes nothing; exit 1 on any problem.

## Work
- `qew create [--kind K] --title T [--parent ID] [--needs ID...] [--label L] [--priority N] [--body TEXT]
  [--body-file F] [--quote Q] [--from-file F]`: a draft unless `--quote` carries a human's words
  (`"Name: 'their words'"`), which files it approved. `--from-file` takes a JSON object or array (or `-`
  for stdin); items may carry `id` (imports keep it), `created`, `ref`, and `@ref` entries in `needs`/`parent`.
  `--from-file` and `--body-file` refuse a symlink (exit 2). `--import` (with `--from-file`) keeps each item's own `events` list, validated as a whole before any write;
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
  Also `[--no-parent] [--kind K] [--add-label L] [--remove-label L] [--body-file F] [--json]`: `--no-parent` removes
  the parent, `--add-label`/`--remove-label` edit the label set (`--labels` replaces it), `--body-file` reads the
  body (a symlink, an empty file, a blank or multi-line title are exit 2). A `needs` change is written as one
  `edge` event per added or removed need, not as a header rewrite, so two branches changing different edges
  merge; other keys rewrite the header with one `field` event each. The files are read back after the write.
  Repeating an edit prints `unchanged`.
- `qew create ... [--draft] [--discovered-from ITEM] [--where T] [--problem T | --goal T] [--fix T | --approach T]
  [--done-when T] [--edit] [--json]`: `--draft` files a draft explicitly (with `--quote` it is exit 2). The structured
  flags render headed sections (`## Where`, `## Problem`, ...); `--problem`/`--goal` and `--fix`/`--approach` are
  exclusive, and `--body`, `--body-file`, `--edit` and the structured flags give the body one way only. `--edit` runs
  `$EDITOR` tokenized as argv, never through a shell. `--discovered-from` stores the origin in the header key
  `discovered_from`; `qew show` mentions it at both ends and it never blocks. A negative `--priority` is exit 2; an actor
  name with whitespace or a comma is refused at the first write. A body, comment or `set --body` with unmarked
  command, flag or path shaped text warns on stderr (`warning: unmarked code-shaped text: X`) and is stored as given.
- `qew comment ITEM (--body TEXT | --body-file F) [--json]`: an append-only `comment` event on any item (blank,
  oversize, symlinked or doubled input is exit 2). `qew show` prints the author (first event's actor), the ten latest
  comments and, in `--json`, all of them.
- `qew block ITEM --on ITEM [--closed] [--json]`, `qew unblock ITEM --from ITEM [--closed] [--json]`: add or remove one
  needs edge as an `edge` event; repeating is `unchanged`; a self edge, unknown item or cycle is exit 2, completed or
  closed items need `--closed`.
- `qew start ITEM [--take-over --reason TEXT] [--lane ID [--workspace PATH] [--kind compute|token]] [--json]`:
  claim approved work whose needs are completed. Another actor's claim is exit 3 naming the holder and
  `--take-over`; your own claim is exit 5 (no event) when the lane matches, exit 3 naming the lane when it does
  not; draft, blocked, completed or closed work is exit 2. `--take-over` writes a forced `released` event naming the
  previous owner and a `claimed` event in one command, only for work that may start and is held by another actor.
  A lane (`A-Za-z0-9._-`, 1 to 64, no `--`) tells lanes of one actor apart; `--workspace` (absolute path) and
  `--kind` need `--lane`. The lane guards `start` and `release` only, as good-faith tooling, not access control.
  After the write is merged with the ledger remote, a claim that lost to an older one exits 3 naming the winner.
  `[claims] max_active_claims` in the config caps the caller's active claims (exit 6, take-over included); a
  malformed cap is exit 2 naming the key. `qew show` prints the current claim as `claim: {by, kind, lane, workspace}`.
- `qew release ITEM --reason TEXT [--force] [--lane ID] [--json]`: give up a claim; another actor's claim (or another
  lane's) needs `--force`, recorded as `forced` with the `previous_owner`. Unclaimed work is exit 2.
- `qew evidence ITEM [--change ID]... [--vcs jj|git]`: record the code changes; default is the current one.
- `qew complete ITEM [--reason TEXT] [--landed-on BRANCH] [--take-over] [--no-commit WHY] [--json]`: refused (exit 3
  naming the holder) unless the caller holds the claim; an already completed item is refused without a second event;
  every child must be completed or closed; and (not for epics) an evidence event whose change IDs all landed on the
  target branch (`--landed-on` overrides the configured one for stacked branches) must exist. The `completed` event
  records the `reason`, the full `commits`, the `branch` and the numbers of the item's pull requests recorded as
  merged (`state: merged` in the `pr` mirror), shown by `qew show`. A change this checkout cannot resolve is
  `unknown to this checkout` (exit 2), a missing target branch is exit 3, unreadable pull request records are exit 4
  (`indeterminate`, nothing written). Hostile values (a branch with `..` or a leading `-`, a blank reason, an
  evidence id that is not a change id) are exit 2 before any VCS call. `--take-over --reason TEXT` finishes another
  actor's claim after the same proof and records `taken_over_from` (nothing to take over is exit 2).
  `--no-commit WHY` completes without code for work delivered outside the repository, only when no evidence exists
  (exactly one of landed evidence or `--no-commit`); `qew check` counts these as `completed without code: N`.
  An epic needs at least one child and no evidence. When a completion or a close finishes the last open child of
  an epic that has at least one completed child, the epic completes in the same command (`children_finished`); a
  failure of that step is a warning naming `qew complete EPIC` and the command still exits 0.
- `qew close ITEM --reason not_planned|duplicate|superseded [--of ITEM | --by ITEM] [--note TEXT] [--json]`:
  `duplicate` needs `--of` (the original), `superseded` needs `--by` (the replacement), `not_planned` takes neither;
  the linked item must exist and differ from the closed one; `qew show` mentions the link at both ends.
  Finished work is not closed as abandoned: `qew close ITEM --delivered-by SHA --landed-on BRANCH [--json]` records
  work that shipped without a claim as completed under the same landing proof (an `evidence` and a `completed`
  event), so dependents unblock; one flag alone, an unlanded commit or another actor's claim is refused first.
- `qew reopen ITEM --reason TEXT [--json]`: completed or closed work returns to approved with the reason recorded;
  an item that is already open prints `unchanged ITEM (already open)`, exit 0, and writes nothing.

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
