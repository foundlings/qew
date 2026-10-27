# qew ledger: design contract (v1)

qew manages **ledger repositories**: offline-first, file-based, mergeable work databases. A ledger
is the source of truth for work items, their dependencies, state and evidence. Forges (GitHub,
Forgejo/Gitea, GitLab) are **mirrors**: qew syncs ledger state to them when reachable and records
what it created. Nothing requires a forge to be up.

## Goals
- Deterministic, mechanical parsing; zero agent tokens to answer "what is ready / blocked / done".
- Merge-conflict-free under many concurrent writers: no shared mutable file.
- Offline-first; forge sync is a separate, retryable step.
- Independent of any one project; one qew serves any number of ledgers.
- Token-efficient agent output (short, fixed formats).

## Repositories
- Code repo (e.g. foundlings): `.qew/config.toml` names its ledger(s) and mirror targets.
- Ledger repo (e.g. foundlings-ledger): holds items, events, mirrors. Any git/jj repo.
- qew itself is managed by qew through `qew-ledger`.

## Ledger layout
```
qew-ledger.toml            # ledger metadata: name, id prefix, schema_version
items/<ID>/item.md         # one file per item, in its own directory: fixed header + human body
events/<ID>/<EVENT-ID>.json  # append-only; one tiny file per event, never edited
mirrors/<ID>/<forge>-<host>-<repo-slug>.json  # one file per (item, mirror target): identity (issue number, node id), written once
baselines/<ID>/<TARGET>/<ID2>.json           # the item's forge-synced state per target: hash, newest synced event, what the forge showed
mirrors/<ID>/<target>-pr-<N>.json            # one per pull request opened for the item (kind: pr; opener, covered items, draft, state)
boards/<target>.json       # board id recorded by `qew project ensure`
views/<name>.json          # named offline board: name, columns, sort, group_by, filter (`qew board create`)
```
- A baseline is committed fact, not machine state: a sync that changes it adds a new file (`<ID2>` is a base35-time id) and
  removes the older ones, so two machines record the same item as two added files and the same deletions, which git merges;
  the newest file wins. A git ledger merges with `-X no-renames`: the old file is deleted and the new one added with nearly the same text, which rename detection would pair into a rename/rename conflict between two machines; a conflict left in `baselines/` or `mirrors/` takes the remote's copy, or removes the path when the remote deleted it. A machine's own pending changes are never stored: they are the item's events newer than the baseline's
  `synced_event`. A clone therefore starts from the last recorded forge state, not from nothing. `.qew/state/TARGET/ID.json`
  (older qew) is read only while an item has no baseline, and the next write moves it.
- No shared index file is committed. `qew index` builds a local cache (gitignored) from files.
- Every write creates a new file or edits only the item's own file → concurrent branches merge cleanly.

## IDs
- Item and event IDs are ledger-local, globally unique, sortable, never forge counters. They are bare (the configured
  `prefix` names the ledger; it is not part of an ID).
- `id_mode` in `qew-ledger.toml` picks the mode per kind (`epic`, `issue`, `task`), either one name for every kind or a
  table; a kind left out gets its default (epics `letters`, issues and tasks `base35-time`; `qew init` writes that table explicitly). A ledger that names `id_mode` keeps what it says:
  - `base35-time` (the default): 9 characters, seconds since 2020 in base 35 plus two more, so IDs sort by creation and
    concurrent writers rarely collide (`qew check` refuses duplicates). Example `042fcpx9w`.
  - `letters`: A, B .. Z, AA .. ZZ, AAA .. in strict order (after ZZZ comes AAAA), for a handful of long-lived items (foundlings uses it for epics, so phases read `ADA`).
  - `counter`: integers; safe for one writer only.
  Imported items keep the IDs they already have. A short unique prefix of an ID resolves.
- Forge issue numbers are recorded only in `mirrors/`.
- `aliases: OLD1 OLD2` (optional header key) lists ids an item had before `qew migrate-ids --to base35-time`. A reference, an
  event or mirror directory, or a forge marker naming an alias means the item that carries it; a leftover file under an
  alias is superseded by the renamed one. Reads canonicalise through the alias map, so a half-finished migration is valid.

## Attachments
- An item owns a directory `items/ID/` that holds its `item.md` and its `attachments/`. Attachment bytes are immutable files
  `items/ID/attachments/HASH.EXT` (sha256 hex plus the original short extension, so a forge web view previews images).
  They are never edited in place, so two machines cannot write the same path with different bytes.
- Names are events: `attached` (`path`, `hash`, `size`, `blob`) and `detached` (`path`). The tree is every event applied
  in ID order; the last event for a path wins, and a path that would be both a file and a directory yields to the later
  event. Concurrent attaches under one name merge without a git conflict and every checkout derives the same winner.
- Detached or replaced bytes stay on disk and in history. `qew check` fails on a missing stored file, a size or hash
  mismatch, a stored file whose directory belongs to no item, and a body link `[text](attachment:NAME)` to a name that is
  not attached. A ledger that uses attachments needs a qew that knows these events; older versions refuse the unknown
  event type loudly.
- Limits: 100 MiB per file (a warning above 25 MiB), 500 MiB of distinct bytes per item; git-lfs is out of scope. jj refuses to snapshot a NEW file over `snapshot.max-new-file-size` (1 MiB by default), which would leave a larger attachment on disk, never committed or pushed; every jj call qew makes therefore passes `--config snapshot.max-new-file-size=128MiB`, a file already refused is committed by the recovery of the next qew write, and `qew check` reports an attachment file that is not in the committed tree (`not committed`).

## Item file (`items/<ID>/item.md`)
Header = first lines, `key: value`, one per line, terminated by a line `---`. Parsers read the
header only and stop at `---` (bounded bytes). Body after `---` is free Markdown for humans.
```
id: 042fcpx9w
kind: epic | issue | task
title: Short imperative title
parent: 042f0gp5t             # optional; epic of an issue, issue of a task
needs: 042f0m67k 042f0ip9w     # dependencies (blocked-by); space-separated IDs
labels: area:erp
fields: {"phase":"ADA","plan":["a","b"]}   # optional (schema 5): named values beside the labels, one JSON line
owner:                         # empty = unclaimed
priority: 2                    # lower = sooner
created: 2026-10-08T16:00:00Z
discovered_from: 042f0gp5t    # optional: where this was found; never a dependency
aliases: 42             # optional: ids before a migration (qew migrate-ids)
evidence: kxyzabcdefgh 1f3a9c7   # completed work: the changes that deliver it, as cited (jj change ids on jj, git commits otherwise)
evidence_absent: no-code: why   # completed work without code: KIND: reason (never together with evidence)
---
Body…
```
Strict evidence (schema 4, the default; `evidence = "lenient"` in `qew-ledger.toml` opts out): a completed item carries
`evidence` (at least one id; each must resolve and be an ancestor of the target) or `evidence_absent` with a kind of `no-code`
(delivered outside the repository), `historical` (done before the ledger or imported without a resolvable id),
`superseded-by:ITEM` or `children` (an epic finished by its children), never silently neither. The header is set by whatever
writes the `completed` event (`evidence` / `absent` + `absent_reason` fields; imports and forge-side closes record `historical`);
events stay the history. The header lists at most 1000 ids (the events keep every one) and may be up to 64 KiB. `qew check` requires the header, that every id is cited by an event, and (where the code repository
is at hand) that every id resolves and landed on the target; the cited change cannot be the one recording the completion
when the ledger lives inside the code repository.

- Allowed keys are fixed by the schema; unknown keys fail validation (loud).
- Reverse edges (blocks, children, referenced-by) are DERIVED, never stored.
- Tasks/checkboxes are child items of `kind: task` (each its own file), not inline Markdown boxes.

## Events (`events/<ID>/<EVENT-ID>.json`)
Append-only facts; current state = fold of events in ID order. One JSON object per file:
`{"type": "...", "at": "...", "actor": "...", ...}`. Types:
- `approved` (with quoted human words), `drafted`
- `claimed` (`owner`; optional `lane`, `workspace`, `kind` = compute|token) / `released` (optional `reason`,
  `previous_owner`, `forced`). Two branches may both claim an item: after the merge the oldest `claimed` event of
  the current claim period (a `released`, `reopened` or `closed` event ends a period) holds the claim, a later
  claim by another actor is a recorded lost race (`qew check` notes `lost claim: ITEM ACTOR`, not an error), and a
  later claim by the same actor is ignored. The merged-claim moment is known only after the fetch, so `qew start`
  re-reads after its write is committed and merged and exits 3 naming the winner when it lost.
- `evidence` (`{"vcs": "jj", "change_ids": [...], "repo": "<code repo url>"}`, plus `on_behalf_of` when another actor recorded it for the claim holder it names)
- `completed` with `"provenance": "import"` (+ a non-empty `original_ids`; `qew check` refuses it without) is written only by an import path
  (`create --from-file --import`) for history without resolvable change ids; `qew check` counts these
  (`imported without evidence: N`) and does not fail on them. Everything else:
- `completed` may carry `bulk_quote` (a human's words; written by `qew complete --manifest`, valid from approved without a claim or evidence), `reason`, `commits` (full ids), `branch`, `prs`, `no_commit`, `taken_over_from`,
  `delivered_by` (work that shipped unclaimed) or `children_finished` (an epic finished by its children); the last
  two may complete an item from approved. `closed` may carry `duplicate_of` or `superseded_by` (one item id) and
  `note`; `reopened` may carry `reason`.
- `completed` (requires: all child tasks completed AND ≥1 evidence event whose change ids resolve
  as ancestors of the target branch in the code repo — checked by `qew complete`, refused otherwise)
- `reopened`, `closed` (`reason`: `not_planned|duplicate|superseded`)
- `field` (header change audit: key, old, new; `fields` is the key for a change of the fields header)
- `author_corrected` (`author`, `via`, `quote`): the newest one decides the item's author; see Authorship. Any event may carry `via`, the tool that wrote it for its actor
- `edge` (`op` add|remove, `need`, `old`, `new`): one needs edge changed; the effective needs are the header list with
  every `edge` event applied in ID order, so branches changing different edges merge. `comment` (`text`): append-only.
An epic's claim is DERIVED at read time, never stored: it is in progress (`qew show`, `list`) while an open direct
non-epic child is claimed, held by exactly those children's actors; a completed or closed epic shows nothing.
- `comment` (`body`; legal in every state, never changes it)
State machine: draft → approved → claimed → completed (closed from any). Illegal transitions refused.

## Reading cost and the snapshot cache
Opening a file costs about 150 microseconds, so reading a large ledger (foundlings: 22,565 items, 165,000 files, 17 to 27 s) is dominated by
opens, not parsing (all of it parses in about 3 s; a warm read now takes about 1.7 s). `.qew/snapshot.db` (SQLite from the standard library; never committed, excluded like the
rest of `.qew/`) caches the TEXT of every item file and event file with a stat signature; a read stats the files (about 0.5 s), re-reads
only those whose signature moved and runs the same parsers on the text, so a cached read means exactly what a file read means. An item file
is trusted while its (mtime, size, inode) is unchanged, an events directory while its (mtime, inode) is (event files are immutable, every
qew write replaces or adds a file); an entry cached less than 2 s after its modification is read again next time (a racy timestamp).
The committed baselines are cached the same way: the newest baseline (file name and text) of each (item, target), with the stat signature of its `baselines/ID/TARGET` directory. A pull that brings in another machine's baseline adds or removes a file there, which moves the signature, so the next lookup re-reads exactly those directories and nothing else (one stat per item and target decides; the rows are written in batches). An events directory is validated (`parse_event` accepts every file) once, when its text enters the cache, and marked so; a later read parses those events without the checks again (parsing 142,000 events was most of the remaining cost), while a directory with any bad file is never marked and is refused on every read, and `qew check` trusts nothing: it re-reads and fully validates every file. A damaged, locked, foreign-format or unwritable database is never an error: the files are read as if there were none. `QEW_NO_CACHE=1`
bypasses it. `qew check` (deep) reads every file itself, reports `snapshot cache: N entries differed from the files (repaired)` and
rewrites what differed. Designed for ledgers up to about 500,000 files. A read command that finds the ledger broken (a writer adds an item's files one after another and a read takes no lock, so an events directory can be seen before its item file) looks again after 0.2, 0.4 and 0.8 s before it refuses with `the ledger is not readable`, so a half-written item is not an error while real damage still is. A snapshot is one pass: `qew start`/`release` of an epic pass it
down (one forge comment per descendant, not one read each), and a read command that finds the write lock held skips its refresh
instead of waiting for it.

A jj command snapshots the working copy (it scans every file: about 1 to 8 s on foundlings) unless given `--ignore-working-copy`, so the
read path never lets it: the fetch, the revset queries and the push use that flag (`ledger_repo.JJ_READ`), a read pushes only when
commits are unpushed, and it looks for files a killed writer left behind only when `.qew/writing` exists (a command creates it when
it takes the write lock to write and removes it when it finishes, even on failure; a kill leaves it). On the loaded live foundlings
ledger one read dropped from 14.7 s (8.5 s of fetch and merge, kernel time 56 s) to 5.1 s (1.5 s).
A write on a jj ledger snapshots the working copy once: its commit. Moving the bookmark ignores the working copy, and the qew-owned files the
commit's snapshot found beyond the paths it named (a killed writer's, a file jj had refused) are swept into a `qew: recover N files` commit
from that same snapshot, so no separate scan precedes the write. When the branch is not an ancestor of the new commit (the working copy sat beside it), the write first merges the two as a `qew: merge` commit instead of being refused as a sideways bookmark move; baseline and mirror conflicts in that merge take the other side, any other conflict stops the write. On a copy of the live ledger the jj time of one write fell from 8.6 s to 2.8 s.

## Mirrors (`mirrors/<ID>/<target>.json`)
`{"forge": "github|forgejo|gitlab", "url": "https://host/owner/repo", "kind": "issue|pr|project_item",
"number": 123, "node_id": "...", "synced_at": "...", "synced_event": "<last event id pushed>"}`
- One item may have mirrors on several forges/repos. Sync is idempotent: a mirror file is written
  only after the forge accepted the create/update. A failed sync leaves no partial file.
- Forge backends implement one interface (create/update issue, set state/labels/assignee, link
  dependency, add to board, read back). GitHub (REST/GraphQL, Projects v2), Forgejo/Gitea (REST),
  GitLab (REST, issue links, boards). Missing capability = typed `Unsupported`, never faked.

## Code repo config (`.qew/config.toml`)
```
[ledger]
url = "git@github.com:foundlings/foundlings-ledger.git"
path = "../foundlings-ledger"   # local checkout
prefix = "FND"
[claims]
max_active_claims = 4           # optional positive integer: `qew start` refuses (exit 6) at this many active claims
                                # of the caller; counted from the folded ledger, so merged branches can briefly exceed it
[[mirror]]                       # any number of targets, on any mix of forges and hosts
forge = "github"
repo = "https://github.com/foundlings/foundlings"
project = 7                      # board number/id where applicable
select = "label:area:erp"        # optional: which items mirror here (default: all)
[[mirror]]
forge = "forgejo"
repo = "https://codeberg.org/foundlings/foundlings"
[[mirror]]
forge = "gitlab"
repo = "https://gitlab.com/foundlings/foundlings-ops"
select = "kind:epic"
```
- `[qew] minimum_version = "MAJOR.MINOR.PATCH"` makes an older qew refuse every command (exit 2, names both
  versions and `scripts/setup_user.py`); malformed values are refused. Unknown keys and mirror options are
  ignored; `schema_version` in `qew-ledger.toml` is strict (missing reads as 1; this qew reads 1 to 7 and works on 4, 5, 6 and 7; any command on a ledger older than 4 refuses naming `qew migrate-layout`, and an older qew refuses 7; `fields` are refused on a schema 4 ledger until it is migrated). Schema 1 and 2 kept each item in `items/<ID>.md` (2 added attachment directories); schema 3 keeps it in `items/<ID>/item.md`, so the ID is only in the directory name and the directory also holds `attachments/`. Schema 4 added the evidence header, schema 5 the `fields` header key and schema 6 `core.untrackedCache` in a git ledger's config (set by `qew migrate-layout` and `qew init`, never on every command), schema 7 the committed `baselines/` (a qew older than 6.31 keeps its sync state local and must not work on such a ledger; `qew migrate-layout` declares 7, the first whole-target `qew sync` writes the baselines). `qew init` writes 7. A symlinked config is refused.
- `project` is a positive integer board number (title strings, numeric strings, 0, negatives, booleans, floats
  are refused at load, exit 2); `project_title` must not be empty. Repository URLs that differ only in case or a
  `.git` suffix are one target (the slug is casefolded).
- Each `[[mirror]]` is an independent target: one item may live on several repositories across
  several forges at once; each gets its own `mirrors/<ID>/<target>.json`. Targets sync
  independently; one failing forge never blocks another.

## Commands (agent-facing; terse output)
- `qew init`, `qew create`, `qew set`, `qew show`, `qew list`, `qew ready`, `qew blocked`, `qew graph`
- `qew pr open|check|ready|merge` (pull requests for items; merge only the checked head), `qew land` (complete after the merge)
- `qew start` (claim), `qew release`, `qew evidence ID --change ...`, `qew complete ID`, `qew close`
- `qew draft list|approve|revert`
- `qew sync [--target ...]` (push ledger → forges, record mirrors; pull forge numbers only)
- `qew project ensure` (board with Draft / Ready / In progress / In review / Done / Closed per mirror target)
- `qew hook install` (claude, codex, cursor, opencode; flags pick a subset)
- `qew check` (validate ledger: schema, cycles, dangling needs, illegal state) — exit non-zero
- `qew import` is NOT in qew: project-specific converters live in the project repo and emit items via `qew create --from-file`.

## Hooks
- Agent hooks (Claude + Codex): SessionStart primes bounded ready-work context; PreToolUse refuses
  bypasses: forge CLI edits of mirrored issues/labels/boards, forge PR merges not through qew,
  completing work without evidence.
- VCS hook in the code repo: on push/merge, a change citing a ledger ID must have its evidence
  recorded; `qew complete` is the only way to mark done (no forge-side close counts).

## Trust
- Forge text is untrusted data; never executed or turned into paths/instructions.
- Fail loudly on malformed files, cycles, truncated reads, partial writes.

## Decisions (v1 builder; the owner was not available)
- `qew create` files into the queue (approved, `filed_by` the creator); a draft only with `--draft`, and only a human's quoted words release a draft (`--quote`, `qew draft approve`).
- Python 3.15+ (`tomllib`) and the standard library only: no third-party packages.
- The header `owner:` stays empty: ownership is derived from `claimed`/`released` events so claims merge cleanly.
  `qew check` refuses a non-empty value.
- Extra event `reverted` (`reason`, `quote`) returns approved, unclaimed work to draft.
- Kinds nest epic > issue > task (a task may sit directly under an epic, and an epic under an epic); `check` refuses other parents and parent cycles. An epic is held while any claimed item sits under it, through nested epics, and completes automatically (unclaimed) when its last child finishes, then the epic above it, and so on. Each epic is the forge sub-issue of its parent epic.
- `complete`: the item must be claimed; every child must be completed or closed; non-epics need an
  `evidence` event all of whose change ids are ancestors of the target branch (`[code] target` in
  `.qew/config.toml`, default `main`; jj revset `::target`, git `merge-base --is-ancestor`). An epic needs
  at least one child and no evidence of its own.
- Readiness: approved, unclaimed, not an epic, every need completed. A dangling or closed need keeps it blocked.
  `qew ready` orders by priority, then the longest chain of unfinished work an item unblocks, then its direct
  unfinished dependents, then id. A needs cycle below ready work is refused (exit 1), never skipped.
- Board columns: Draft = not startable (draft or blocked), Ready, In progress (claimed), In review (claimed with a
  pull request mirror), Done (completed), Closed (closed: not planned, duplicate or superseded).
- Forge issue bodies start with `<!-- qew:ID -->`; sync adopts an issue found by that marker when a crash lost
  the mirror file. Sync replaces an issue's whole label set with the item's labels plus `P0`..`P4`
  (the priority, larger values clamp to `P4`), `author:LOGIN` (the forge login the mirror maps the author to, `[forge] login` / `assignees`, else the author's own name), `epic` / `task` for those kinds (a plain issue has no kind
  label) and the status label of its column (`ready`, `in-progress`, `gated`; forges.md, Status labels). Repository mode stops there: the Status is the board's field and state is the issue's open/closed, so no `status:` or
  `qew:` label. Overlay mode adds `qew:managed`, `qew:STATE` and `status:COLUMN`. The retired spellings `kind:KIND` and
  `priority:N` are managed namespaces: they are never read back as a human's labels and the next sync removes them
  (`qew sync --prune-labels` deletes their definitions). Labels are one word of at most 50 printable characters, without
  commas, and never start (casefolded) with `qew:`, `kind:`, `priority:`, `status:` or `author:` nor equal `epic`, `task`, `ready`, `in-progress` or
  `P0`..`P4`: create, set, imports refuse them (exit 2, "reserved") before any write and `qew check` flags them in files.
  Label colours: see forges.md.
- Authorship: an item's author is the actor of its first event unless an `author_corrected` event (`author`, `via`, `quote`) says otherwise (the newest wins). An event may carry `via`, the tool that wrote it for its actor (an importer). Corrections are appended by `qew correct-author`; no event is rewritten.
- Fields (header key `fields`, schema 5): named values beside the labels, one compact JSON object on one line (`{"phase":"12","plan":["a","b"]}`), omitted when the item has none. A name is lower-case letters, digits, `-` and `_` (at most 32, starting with a letter); a value is one printable line of at most 200 characters; a field holds 1 to 50 values in order. Fields are never labels: sync does not mirror them as labels. A mirror's `project_fields` map shows them on the board (forges.md). `qew create --field`, `qew set --field`/`--clear-field`, `set --manifest` rows and plan-file entries write them; each change is a `field` event with key `fields`; `qew list --field`, `qew search`, `qew show` and `--json` read them.
- Sync is push-only; forge numbers are recorded, forge state is never read back as truth.
- `qew index` (local cache) is not built: reads are fast enough from files.
- Pull requests never carry a closing keyword.

## Replication (self-updating ledger, bidirectional forge sync)

The ledger records and replicates itself; the person or agent never remembers to commit, push or sync.

On a jj ledger, empty undescribed checkpoints do not block automatic pushes; their commit and change IDs
stay intact. Undescribed commits containing changes still fail jj's push validation, as do conflicts and private commits.
Everything below is best-effort and offline-first: no network failure ever fails or blocks a write.

1. **Every write commits itself.** A write command (`create set start release evidence complete close reopen
   draft approve|revert accept reject import restore` and any other that changes ledger files) writes its files
   and commits exactly those paths to the ledger repo (jj or git, detected like the code repo) with the message
   `qew: <verb> <ID> [by <actor>]` (`qew: <verb> <N> items` for a batch). Atomic: files are written to temp
   names, linked into place, committed, and if the commit fails the files written by this command are removed or
   restored, so there is no uncommitted-write state. A crash between write and commit leaves files that the next
   qew command notices (`qew status` shows them as `uncommitted`) and commits as `qew: recover <paths>`.
2. **Ledger remote.** Before a write: best-effort fetch and merge of the ledger remote (timeout
   `[replication] timeout_s`). After a write: best-effort push. Unreachable remote: local commits stay and the next
   command or `qew sync` retries. Merges cannot conflict by construction (one file per item and event, unique
   ids). A real text conflict is a ledger bug: reported loudly, never auto-resolved, writes refuse until fixed.
3. **Forge push, all targets.** After a write qew opportunistically syncs the affected items to every mirror
   target within a time budget, each target isolated (one forge down never blocks another or the write).
   Pending work is derived, never queued: an item is pending for a target when it is selected by that target and
   has no mirror file, or its newest event id is greater than the mirror's `synced_event`. No outbox files.
4. **Forge pull and import.** `qew import --from TARGET` (a configured target, or ad hoc `--forge F --repo URL`)
   reads issues, pull requests, boards, labels and comments and creates or updates ledger items, writing mirror
   files and events with provenance `forge-import`. `qew sync` also pulls incrementally: forge-side edits made
   outside qew become events with provenance `forge`.
   - Content (title, body, labels) merges as `field` events; comments become `commented` events; new forge
     issues become items (drafts unless the forge item is closed, which imports as `closed` or, with `--history`,
     completed with provenance `import`).
   - A forge-side STATE change (closed, reopened, assignee taken, moved on the board) never overrides ledger
     state: it is recorded as a `forge_divergence` event (`target`, `field`, `forge`, `ledger`, `remote_ref`).
     `qew status` lists unresolved divergences; `qew accept DIVERGENCE` applies the forge value through the normal
     gates (a completion needs evidence, a release needs a human quote) and writes `divergence_accepted`;
     `qew reject DIVERGENCE` keeps the ledger value, writes `divergence_rejected`, and the next push restores the
     forge. Divergence ids are the event ids.
   - After a pull, the change is pushed to the other targets. Forge text is untrusted data.
5. **Restore both ways.** `qew restore --to TARGET` recreates issues and the board from the ledger alone;
   `qew import` rebuilds a ledger from forges alone. Both are idempotent and record mirrors.
6. **Visibility.** `qew status [--json]`: uncommitted files, unpushed ledger commits, pending items per target,
   unresolved divergences, last successful sync per target (from mirror `synced_at`), online or offline.
7. **Config.** `.qew/config.toml`: `[replication] auto_commit = true, auto_push = true, auto_forge_sync = true,
   timeout_s = 30`. `QEW_OFFLINE=1` forces offline: no fetch, push or forge calls (writes still commit locally).
   Agent hooks never block qew's own replication.
8. **Concurrency.** See "Concurrency" below: kernel locks that die with their holder, idempotent verbs, repository
   locks named and never deleted. Several machines write concurrently through the VCS merge.

New event types: `commented` (`text`, `author`, `remote_ref`), `forge_divergence`, `divergence_accepted`,
`divergence_rejected`; `provenance` may be `import`, `forge-import` or `forge` where the event came from a forge.

### Concurrency (ten agents on one machine)
Decision: the primitive is a kernel advisory lock, nothing else, so nothing is ever stale.
- **One module, `filelock.py`** (stdlib only): POSIX `flock` on the whole file, Windows `msvcrt.locking` on one byte far
  past the holder text. No other module imports `fcntl`/`msvcrt` or calls `flock` (a source gate in
  `tests/test_filelock_walk.py` fails otherwise). Pid files and `O_EXCL` sentinel files are never used as locks.
- **Death releases.** The kernel frees the lock when the holder ends by any means, `kill -9` and power loss included. The
  lock FILES (`.qew/lock` write lock, `.qew/push.lock` push lock, `.qew/sync.lock` worker) stay on disk, inert; nothing
  detects, ages or clears them. They live in the VCS-excluded `.qew/` cache.
- **The holder text is courtesy only.** The holder writes `{pid, host, verb, who, since}` into the file after it locks;
  a waiter reads it only to word the timeout (`the ledger X is locked by agent3's `complete` (pid 123, 12s)`, exit 3).
  It is never consulted to decide whether the lock is free.
- **Waiting.** Bounded by `[replication] lock_wait_s` (60; `QEW_LOCK_WAIT_S` overrides it for one command) with
  exponential backoff (10 ms to 250 ms) plus jitter, so ten waiters do not stampede. Waiters are served in arrival order:
  one that finds the lock taken leaves `LOCK.waiting.PID.THREAD.ARRIVAL` beside it, touches it while it waits, and tries
  the lock only when no earlier waiter is live. The queue is a courtesy over the kernel lock, never a second lock: a marker
  of a dead process or one untouched for 5 s is ignored, and a waiter yields to earlier ones for at most 20 s, then tries
  regardless, so no waiter can hold up another for good. The timeout names how many waited ahead. Locks are not re-entrant: a nested hold
  on one path times out loudly. Push runs outside the write lock; the worker's own commit waits on the push lock.
- **Idempotent verbs.** A repeated command after a crash, timeout or agent retry never duplicates:
  `create --key KEY` (same key and content returns the first item with the same line, a key under other content is exit 2;
  it reuses the plan adoption markers, so a run killed between its event and its item file is adopted),
  `comment --key KEY` (same key and text posts nothing, `(unchanged)`), `evidence` for a change already recorded,
  `draft approve` with the words that already released it, `release` and `close` repeated by the same actor, and
  `complete` of completed work all print `(unchanged)` with exit 0 and write nothing. `start` by the holder is the
  established exit 5. A different quote on live work, or another reason on closed work, stays a loud refusal (exit 2).
  Events are appended under the write lock with the next id after the newest, created exclusively (`os.link`), so a replay
  never overwrites one.
- **Repository locks.** git keeps `index.lock` and ref locks as sentinel files, so a crashed git leaves one behind. qew's
  git/jj calls retry on a lock error with backoff up to `QEW_REPO_LOCK_WAIT_S` (default 20). A lock older than 60 s that no
  process has open (`lsof`; without it, no git process on the machine) is reported as stale (exit 3, file named and age) and
  left in place; a lock still held after the wait is reported as held. qew never deletes a repository lock on its own:
  `qew unlock --stale --quote Q` removes the dead ones on a human's approval, logs each removal to `.qew/unlock.log`, and
  refuses young or open locks and any running qew writer. jj's working-copy
  and operation locks are kernel locks held for one command, so a dead jj frees them and jj itself waits.
- **Remote refs.** A fetch and a push of one checkout both rewrite `refs/remotes/origin/*`; run together they fail with
  `Failed to update refs` and the claim arbiter would read that as an unreachable remote. Every fetch and push takes the
  innermost `.qew/remote.lock` (held for that one network operation only, never while waiting for another lock, so it
  cannot deadlock with the write or push lock). The last failure text is kept in `.qew/remote-error` and named in the
  `cannot claim ... unreachable (WHY)` message.
- **Walks.** `tests/test_ten_agents_walk.py` (git) and `tests/test_ten_agents_jj_walk.py` (jj) run ten agents, five first attempts killed 0.2 s in, with a bare remote, kills
  writers mid-session, and asserts one item per key, no tracebacks, clean history, an unblocked fresh writer and a
  converged remote. Not covered: network filesystems (their lock semantics vary), and Windows is exercised through a
  stubbed `msvcrt`, not a Windows run.

### Background sync

- **Offline first, forge second (decision).** A write is complete when the ledger commit lands: local, conflict-free
  (base35-time ids), never waiting for a forge. The issue and its board card are step two, derived and idempotent, moved by
  qew itself with no board workflow and no `qew sync` or `qew project sync` to run by hand. The forge or board is
  never written first. Only a claim under a primary forge arbitrates remotely (see Authority).
- After any write qew queues the ids the write touched in `.qew/sync.queue` (a union, each id once) and starts ONE
  detached worker unless one runs. The worker drains the queue and syncs only those items plus the cards their
  change moves (dependents, parent epics): it reads each item's issue and card one by one, reuses the recorded
  board, looks for an existing issue only among the newest page, and never lists every issue or every card, so a
  create, start or complete reaches the board in seconds whatever the ledger size. Ten quick writes make one run.
  Where the seconds go, and what was cut (live, `foundlings/qew`: a draft's card showed about 5 s after the command
  started, down from about 8 s): the write STARTS ITS WORKER BEFORE its ledger push (the worker's own ledger commit
  waits on `.qew/push.lock`, held by the write for the spawn and the push, so the two never touch the checkout at
  once); a new issue is created without a listing first and the duplicate guard (lowest number wins, the other closes
  and loses its card) runs after the card is placed; Status and Priority are set in one GraphQL request; a card that
  was just created is not read before it is placed (the read-back stays); the project structure is cached per target
  in `.qew/state/TARGET/board-layout.json` (never committed) and re-read once when GitHub refuses a cached id.
  `QEW_TRACE=1` prints every phase and HTTP call with its wall time on stderr (a worker's stderr is `.qew/sync.log`).
  After a failed pass, or with an empty queue, the worker syncs the whole target (`qew sync`, `qew sync --full`);
  a human-filed issue is imported and gets its card (Draft: it is not approved) on that full pull.
  **Crash orphans.** Before it creates a forge issue the sync records the item in `.qew/state/TARGET/creating.json`
  (local, never committed) and clears it once the mirror is written. A create the forge applied but whose mirror was
  never recorded leaves that intent behind, and the next sync of the item then looks through ALL issues for its marker
  before it creates anything (so an orphan pushed out of the newest 100 is adopted, not duplicated). Pull then treats
  the mirror as the authority: an open issue carrying an item's marker that is not the mirrored issue closes (marker
  rewritten to `qew-duplicate`), at once when its number is higher or the item is finished, and when it is LOWER only
  once the mirror's `synced_at` is more than 10 minutes old (two checkouts that created twins at the same moment would
  otherwise each close the other's, before the ledger merge picks one mirror). An item with no mirror adopts its
  lowest-numbered open marker issue on a complete listing and closes the rest.
- Write verbs answer in ONE compact line and print nothing about syncing: `ID claimed by OWNER: TITLE`,
  `ID completed; ready: ID...` (the work the completion released), `ID approved`. The backlog is in `qew status`.
- The worker takes a lock in the ledger checkout's `.qew/` cache dir (VCS-ignored), writes `.qew/sync.log`
  (bounded, rotated), and stops after `timeout_s` per target. A second writer finding the lock held does nothing
  (no double sync); a worker that dies frees its lock with its process (see Concurrency), nothing is cleared.
- Offline or failing forges leave the derived pending state; the next command retries silently and
  `qew status` shows the backlog and the last log line. `[replication] background_sync = true` (default);
  `QEW_OFFLINE=1` or `background_sync = false` disables it. Tests and `qew sync` run the same worker in the
  foreground.

### Replication keys and files (decisions)

`[replication]` keys: `auto_commit`, `auto_push` (ledger fetch/merge/push), `auto_forge_sync`, `background_sync`
(booleans, default true), `lock_wait_s` (60; how long a command waits for the ledger lock), `timeout_s` (30; a fetch or push that takes longer is a timeout, not an outage: the warning says `timed out after Ns`, names the setting and counts the commits other machines cannot see, and `qew status` keeps the reason as `ledger remote error:`), `fetch_interval_s` (10: reads fetch at most this often; writes always; `qew pull` and a lookup that misses an id always),
`sync_retry_s` (10: a read restarts a worker that left work), `sync_budget_s` (20: inline sync time budget), `forge_concurrency` (4: forge writes in flight at once; 1 to 32), `divergences` (`"ledger"`: sync undoes forge-side changes itself, because the ledger is the source of truth; `"report"`: they wait for `qew accept` or `qew reject`), `sync_limit` (300: pending writes an unscoped `qew sync` makes before it asks for `--all` or a scope; 0 lifts it), `forge_failure_limit` (5: consecutive items failing on a 5xx or a lost connection before the target stops; 1 to 1000),
`branch` (`main`). A jj command gets at least 60 seconds whatever `timeout_s` says (it snapshots the whole working copy first; `QEW_JJ_TIMEOUT_S` changes the floor). A fetch or push that still times out counts as an unreachable remote: a warning on stderr, the commit stays local, `qew status` shows `remote=offline`, and the next command catches it up; the command itself keeps its exit code. Unknown keys fail. Cache files in the ledger checkout's `.qew/` (excluded from the VCS):
`lock` (write lock), `sync.lock` (worker), `sync.log`, `sync.pending`, `sync.spawned`, `last-fetch`.
A `sync` run takes the write lock only to commit (every 25 mirror writes and at the end), never during forge calls.
A write of any size is one commit: git receives the paths on stdin, and jj, past a size that fits a command line, the owned directories. A commit that fails rolls back every file and per-item directory the write created.

## Authority and topology

Intent: the ledger is the mind and travels freely; exactly ONE authority decides contested facts (who owns what), so
offline work never produces two owners. GitHub and Forgejo/Gitea are first-class; GitLab stays as it is.

- **Primary forge per ledger.** `.qew/config.toml` `[authority] primary = "<target slug>" | "none"` (default `none`).
  - `primary = <target>`: that forge arbitrates CLAIMS. `qew start` needs it online and makes an atomic reservation
    there: qew posts a claim comment `<!-- qew:claim ITEM OWNER NONCE -->` plus a visible line (`OWNER claimed ITEM.`, so GitHub shows no empty card) on the mirrored issue and re-reads the
    comments; the earliest claim comment (forge order, not timestamps) wins. A lost race writes nothing and refuses.
    On success the ledger gets `claimed` plus `claim_confirmed`. Release posts `<!-- qew:release ITEM -->` and `Released ITEM.`. A pull never imports these as comments: they are arbitration, not discussion.
    An unreachable primary refuses `start` unless the offline override is used (see Offline claims).
  - `primary = "none"`: the LEDGER REMOTE arbitrates. `start` writes `claimed`, pushes, and the claim counts once the
    push lands (`claim_confirmed`). A rejected push fetches and merges; if another claim for the item is already
    there, qew writes `claim_lost` (`ref` = the lost claim event, `winner`) and the caller does not own the item. A
    ledger without a remote is single-writer: the claim confirms at once. A configured but unreachable remote
    refuses `start` unless the offline override is used (see Offline claims).
- **Offline claims (explicit override).** For private repositories worked offline and settled later. `qew start ITEM
  --offline` skips the arbiter and writes `claimed` with `provisional: true`; `[authority] offline_claims = "allow"`
  (default `"refuse"`) makes a plain `start` do the same, only when the arbiter is unreachable or the primary has no
  issue for the item yet. A provisional claim is an ordinary claim for working offline: evidence, comments and
  `complete` work. `qew complete` under one records `provisional_claim` (the claimed event id) on `completed`; the
  fold lets such a completion stand even if the claim is later lost. Every `qew sync` (and the background worker)
  settles each provisional claim, whatever `--target`/`--item` it was given aside: with a primary forge by reading the
  claim comments of the item's issue (none in force: post the owner's claim comment; the holder is the owner: confirm;
  another holder: lose); with the ledger remote by comparing against other owners' claims in the same claim period
  (a rival that is confirmed, or older, wins); a ledger without a remote confirms at once. Confirmed: `claim_confirmed`.
  Lost: `claim_lost` with `winner`, `provisional: true` and a reason; `qew sync` prints `claim lost: ITEM is held by
  WINNER` and exits 3, and `qew status` keeps a `claim ITEM lost winner=WINNER` line until the item is re-claimed,
  completed or closed. An arbiter that is still unreachable leaves the claim provisional (`claim provisional:` line,
  exit 0). `--offline` cannot be combined with `--take-over`.
- **Scope.** Claiming an epic claims its open descendants in one operation (all-or-nothing with rollback). Holding an
  item or its epic allows working it offline: evidence, comments, progress, complete (needs evidence only; syncs
  later). Evidence and completion by someone who does not hold the item or an ancestor are refused.
- **Conflicts.** Content merges (one file per event). Contested facts (owner, state) follow the authority rule above,
  never timestamps or last writer. In the fold a `claimed` event named by a `claim_lost` is ignored. Remaining
  contradictions surface in `qew status` as divergences, and `qew conflicts` lists them all for `qew resolve`.
  Conflict inventory: needs a decision (listed): `ledger_merge` (same ledger file changed on two checkouts; settled whole
  file with `qew resolve ledger --take ours|theirs`, ledger-owned files only, the discarded side recorded in a
  `conflict_resolved` event), `divergence` (`--take forge|ledger`), `claim_contest` (`--take forge|ledger`). Settles itself
  (not listed): a rejected push (fetch, merge, retry), mirror-only merge conflicts (remote copy), duplicate forge issues
  (the pull closes the later one), a lost claim (final; `claim_lost` names the winner), a stale or unmirrored item
  (`qew status` pending; the next sync). A merge conflict stops every fetching command (exit 1) until settled.
- **Secondary mirrors are read-only.** Every target but the primary is a one-way mirror (ledger -> forge): pull never
  imports their edits (a state edit there may be flagged as a divergence). `qew mirror add FORGE REPO [--read-only]`,
  `qew mirror remove TARGET`, `qew mirror list` edit `[[mirror]]` entries in `.qew/config.toml`.
- **Mappings stay out of item files.** Item headers hold only ledger-level links (`tracks:`, `pr:` = ledger ids of the
  tracking issue or pull request item; `parent:` as before), never forge numbers or URLs. Forge mappings live only in
  `mirrors/` (the per-target index). `qew show` resolves links to forge URLs for display.
- **Repos pushed everywhere.** Each forge target also receives the code repo and the ledger repo (git remote
  `qew-<target slug>` per target, pushed by sync), so any forge can become primary.
- **Forge migration.** `qew forge migrate --to TARGET`: restore the full ledger onto the target (issues, board, pull
  requests as links), verify counts, switch `primary` to it and demote the old primary to a read-only mirror
  (`--remove-old` drops it). Idempotent and resumable (progress is derived from mirror files).
- **Ledger fork.** `qew ledger fork --to REMOTE [--primary TARGET]` creates a new ledger identity with lineage
  (`forked_from = "<ledger id>@<commit>"`, `ledger_id` in `qew-ledger.toml`). Items keep their ids, events stay;
  mirrors, boards, claims and open divergences are NOT carried over. Re-merging a fork into its origin is refused.

New event types: `claim_confirmed` (`ref`), `claim_lost` (`ref`, `winner`).

### Mirror mode (repository or overlay)

`[[mirror]] mode = "repository"` (default): qew owns the whole tracker; synced issues carry no `qew:managed` label and board
views are unfiltered. `mode = "overlay"`: the tracker is shared with humans; synced issues carry `qew:managed` and every board
view filters on it. A `select` expression only narrows which items mirror; it no longer implies overlay. Switching a target to
repository mode removes the stale `qew:managed` labels on the next sync (the label set is replaced).
