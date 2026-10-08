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
mirrors/<ID>/<target>-pr-<N>.json            # one per pull request opened for the item (kind: pr)
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
- `completed` with `"provenance": "import"` (+ `original_ids`) is written only by an import path
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
- `qew pr open|ready|merge` (pull requests for items; merge only the checked head)
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
- Board columns: Draft = not startable (draft or blocked), Ready, In progress (claimed), In review (claimed with a
  pull request mirror), Done (completed or closed).
- Forge issue bodies start with `<!-- qew:ID -->`; sync adopts an issue found by that marker when a crash lost
  the mirror file. Sync replaces an issue's whole label set with the item's labels plus `qew:STATE`,
  `kind:KIND`, `priority:N`. Labels may not contain whitespace or commas.
- Sync is push-only; forge numbers are recorded, forge state is never read back as truth.
- `qew index` (local cache) is not built: reads are fast enough from files.
- Pull requests never carry a closing keyword.
