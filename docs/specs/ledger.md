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
items/<ID>.md              # one file per item: fixed header + human body
events/<ID>/<EVENT-ID>.json  # append-only; one tiny file per event, never edited
mirrors/<ID>/<forge>-<host>-<repo-slug>.json  # one file per (item, mirror target)
mirrors/<ID>/<target>-pr-<N>.json            # one per pull request opened for the item (kind: pr; opener, covered items, draft, state)
boards/<target>.json       # board id recorded by `qew project ensure`
```
- No shared index file is committed. `qew index` builds a local cache (gitignored) from files.
- Every write creates a new file or edits only the item's own file → concurrent branches merge cleanly.

## IDs
- Item and event IDs are ledger-local, globally unique, sortable, never forge counters:
  `<PREFIX>-<ULID>` (e.g. `FND-01JA7…`). A short alias (first 8 chars after prefix) resolves when unique.
- Forge issue numbers are recorded only in `mirrors/`.

## Item file (`items/<ID>.md`)
Header = first lines, `key: value`, one per line, terminated by a line `---`. Parsers read the
header only and stop at `---` (bounded bytes). Body after `---` is free Markdown for humans.
```
id: FND-01JA7Q3M
kind: epic | issue | task
title: Short imperative title
parent: FND-01JA7P00          # optional; epic of an issue, issue of a task
needs: FND-01JA7Q1A FND-01JA7Q2B   # dependencies (blocked-by); space-separated IDs
labels: area:erp phase:ADA
owner:                         # empty = unclaimed
priority: 2                    # lower = sooner
created: 2026-10-08T16:00:00Z
---
Body…
```
- Allowed keys are fixed by the schema; unknown keys fail validation (loud).
- Reverse edges (blocks, children, referenced-by) are DERIVED, never stored.
- Tasks/checkboxes are child items of `kind: task` (each its own file), not inline Markdown boxes.

## Events (`events/<ID>/<EVENT-ID>.json`)
Append-only facts; current state = fold of events in ID order. One JSON object per file:
`{"type": "...", "at": "...", "actor": "...", ...}`. Types:
- `approved` (with quoted human words), `drafted`
- `claimed` / `released` (owner)
- `evidence` (`{"vcs": "jj", "change_ids": [...], "repo": "<code repo url>"}`)
- `completed` with `"provenance": "import"` (+ a non-empty `original_ids`; `qew check` refuses it without) is written only by an import path
  (`create --from-file --import`) for history without resolvable change ids; `qew check` counts these
  (`imported without evidence: N`) and does not fail on them. Everything else:
- `completed` (requires: all child tasks completed AND ≥1 evidence event whose change ids resolve
  as ancestors of the target branch in the code repo — checked by `qew complete`, refused otherwise)
- `reopened`, `closed` (`reason`: `not_planned|duplicate|superseded`)
- `field` (header change audit: key, old, new)
State machine: draft → approved → claimed → completed (closed from any). Illegal transitions refused.

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
- Each `[[mirror]]` is an independent target: one item may live on several repositories across
  several forges at once; each gets its own `mirrors/<ID>/<target>.json`. Targets sync
  independently; one failing forge never blocks another.

## Commands (agent-facing; terse output)
- `qew init`, `qew create`, `qew set`, `qew show`, `qew list`, `qew ready`, `qew blocked`, `qew graph`
- `qew pr open|check|ready|merge` (pull requests for items; merge only the checked head), `qew land` (complete after the merge)
- `qew start` (claim), `qew release`, `qew evidence ID --change ...`, `qew complete ID`, `qew close`
- `qew draft list|approve|revert`
- `qew sync [--target ...]` (push ledger → forges, record mirrors; pull forge numbers only)
- `qew project ensure` (board with Draft / Ready / In progress / In review / Done per mirror target)
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
- `qew create` files a draft; `--quote` (a human's words) files it approved. Only quoted words release work.
- Python 3.11+ (`tomllib`); no runtime dependencies.
- The header `owner:` stays empty: ownership is derived from `claimed`/`released` events so claims merge cleanly.
  `qew check` refuses a non-empty value.
- Extra event `reverted` (`reason`, `quote`) returns approved, unclaimed work to draft.
- Kinds nest epic > issue > task (a task may sit directly under an epic); `check` refuses other parents.
- `complete`: the item must be claimed; every child must be completed or closed; non-epics need an
  `evidence` event all of whose change ids are ancestors of the target branch (`[code] target` in
  `.qew/config.toml`, default `main`; jj revset `::target`, git `merge-base --is-ancestor`). An epic needs
  at least one child and no evidence of its own.
- Readiness: approved, unclaimed, not an epic, every need completed. A dangling or closed need keeps it blocked.
  `qew ready` orders by priority, then the longest chain of unfinished work an item unblocks, then its direct
  unfinished dependents, then id. A needs cycle below ready work is refused (exit 1), never skipped.
- Board columns: Draft = not startable (draft or blocked), Ready, In progress (claimed), In review (claimed with a
  pull request mirror), Done (completed or closed).
- Forge issue bodies start with `<!-- qew:ID -->`; sync adopts an issue found by that marker when a crash lost
  the mirror file. Sync replaces an issue's whole label set with the item's labels plus `qew:STATE`,
  `kind:KIND`, `priority:N`, `author:LOGIN`. Labels are one word of at most 50 printable characters, without
  commas, and never start (casefolded) with `qew:`, `kind:`, `priority:` or `author:`: create, set, imports
  refuse them (exit 2, "reserved") before any write and `qew check` flags them in files.
- Sync is push-only; forge numbers are recorded, forge state is never read back as truth.
- `qew index` (local cache) is not built: reads are fast enough from files.
- Pull requests never carry a closing keyword.

## Replication (self-updating ledger, bidirectional forge sync)

The ledger records and replicates itself; the person or agent never remembers to commit, push or sync.
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
   timeout_s = 5`. `QEW_OFFLINE=1` forces offline: no fetch, push or forge calls (writes still commit locally).
   Agent hooks never block qew's own replication.
8. **Concurrency.** A lock file in the ledger checkout (`.qew.lock`, ignored by the VCS, advisory flock) serializes
   writers on one machine; several machines write concurrently through the VCS merge.

New event types: `commented` (`text`, `author`, `remote_ref`), `forge_divergence`, `divergence_accepted`,
`divergence_rejected`; `provenance` may be `import`, `forge-import` or `forge` where the event came from a forge.

### Background sync

- After any write (and when pending work exists at the start of a command) qew starts ONE detached background
  process that pushes the ledger remote and syncs every mirror target; the foreground command returns at once and
  prints at most one short line (`sync: queued`), never sync output, so agents spend no tokens on it.
- The worker takes a lock in the ledger checkout's `.qew/` cache dir (VCS-ignored), writes `.qew/sync.log`
  (bounded, rotated), and stops after `timeout_s` per target. A second writer finding the lock held does nothing
  (no double sync); a worker that dies leaves its stale lock, which the next command detects by pid and clears.
- Offline or failing forges leave the derived pending state; the next command retries silently and
  `qew status` shows the backlog and the last log line. `[replication] background_sync = true` (default);
  `QEW_OFFLINE=1` or `background_sync = false` disables it. Tests and `qew sync` run the same worker in the
  foreground.

### Replication keys and files (decisions)

`[replication]` keys: `auto_commit`, `auto_push` (ledger fetch/merge/push), `auto_forge_sync`, `background_sync`
(booleans, default true), `timeout_s` (5), `fetch_interval_s` (2: reads fetch at most this often; writes always),
`sync_retry_s` (10: a read restarts a worker that left work), `sync_budget_s` (20: inline sync time budget),
`branch` (`main`). Unknown keys fail. Cache files in the ledger checkout's `.qew/` (excluded from the VCS):
`lock` (write lock), `sync.lock` (worker), `sync.log`, `sync.pending`, `sync.spawned`, `last-fetch`.
A `sync` run takes the write lock only to commit (every 25 mirror writes and at the end), never during forge calls.

## Authority and topology

Intent: the ledger is the mind and travels freely; exactly ONE authority decides contested facts (who owns what), so
offline work never produces two owners. GitHub and Forgejo/Gitea are first-class; GitLab stays as it is.

- **Primary forge per ledger.** `.qew/config.toml` `[authority] primary = "<target slug>" | "none"` (default `none`).
  - `primary = <target>`: that forge arbitrates CLAIMS. `qew start` needs it online and makes an atomic reservation
    there: qew posts a claim comment `<!-- qew:claim ITEM OWNER NONCE -->` on the mirrored issue and re-reads the
    comments; the earliest claim comment (forge order, not timestamps) wins. A lost race writes nothing and refuses.
    On success the ledger gets `claimed` plus `claim_confirmed`. Release posts `<!-- qew:release ITEM -->`.
    No offline claims: an unreachable primary refuses `start`.
  - `primary = "none"`: the LEDGER REMOTE arbitrates. `start` writes `claimed`, pushes, and the claim counts once the
    push lands (`claim_confirmed`). A rejected push fetches and merges; if another claim for the item is already
    there, qew writes `claim_lost` (`ref` = the lost claim event, `winner`) and the caller does not own the item. A
    ledger without a remote is single-writer: the claim confirms at once. A configured but unreachable remote
    refuses `start` (no offline claims).
- **Scope.** Claiming an epic claims its open descendants in one operation (all-or-nothing with rollback). Holding an
  item or its epic allows working it offline: evidence, comments, progress, complete (needs evidence only; syncs
  later). Evidence and completion by someone who does not hold the item or an ancestor are refused.
- **Conflicts.** Content merges (one file per event). Contested facts (owner, state) follow the authority rule above,
  never timestamps or last writer. In the fold a `claimed` event named by a `claim_lost` is ignored. Remaining
  contradictions surface in `qew status` as divergences for `qew accept` / `qew reject`.
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
