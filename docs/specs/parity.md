# Parity with the reference implementation

Every command and flag of the reference tool either exists in qew under the same name, or is listed here with the qew
replacement or the reason it was dropped. The reference stored work in GitHub labels; qew stores it in a ledger, so
label-management commands have no counterpart. `tests/test_parity_walk.py` checks that every backticked qew command below names a
real command and real flags.

## Same name
`block`, `blocked`, `close`, `comment`, `complete`, `create`, `draft approve|list|revert`, `graph`, `hook`, `inbox`, `init`,
`land`, `list`, `plan status`, `pr check|merge|ready`, `prime`, `project ensure|prune|status`, `ready`, `release`, `reopen`,
`search`, `set`, `show`, `start`, `triage`, `unblock`.

## Commands with a different shape
| Reference | qew | Note |
|---|---|---|
| `adopt` | `qew import` | foreign issues become drafts; no managed-label adoption exists (repository mode adds no labels) |
| `apply` (JSONL plan) | `qew create --from-file` | JSON object or array, `ref`/`@ref` links, `--import` for events |
| `area`, `area ensure` | `qew set --add-label` | labels are free text on items; sync replaces the forge label set, nothing to ensure |
| `author`, `author backfill` | `qew sync` | the `author:` label is derived from the first event; the next sync relabels every issue |
| `campaign create` | `qew set --due` | an epic is the campaign; a mirrored epic becomes the forge milestone |
| `campaign status` | `qew plan status` | roll-up of the epic's children |
| `campaign complete` | `qew complete` | an epic completes when its children are done |
| `graph check` | `qew check` | cycles, contradictions and graph invariants are part of check |
| `hooks`, `hooks install`, `hooks remove` | `qew hook install`, `qew hook remove` | one `hook` group |
| `plan create` | `qew create --kind epic` | children join with `qew set --parent` |
| `plan complete` | `qew complete` | same gate as any item |
| `pr` (opens a pull request) | `qew pr open` | a subcommand, same options |

## Flags with a different shape
| Reference flag | qew | Note |
|---|---|---|
| `close --abandoned` | `qew close --reason` | `not_planned` |
| `close --duplicate-of`, `--superseded-by` | `qew close --reason --of` | `duplicate` or `superseded` plus the other item |
| `close --completed` | `qew complete` | completion is gated, never a close flavor |
| `complete --commit` | `qew evidence --change` | evidence names the change; there is no commit message flag |
| `create --approved` | `qew create --quote` | a human's quote files the item approved |
| `create --area` | `qew create --label` | |
| `create --blocked-by` | `qew create --needs` | |
| `create --needs-approval`, `set --needs-approval`, `--no-needs-approval` | `qew set --add-label` | the `needs:approval` label puts the item in the Needs approval column |
| `create --type`, `set --type` | `qew create --kind` | kind is epic, issue or task |
| `init --mode` | `qew init` | mirror `mode` lives in `.qew/config.toml` (`repository` or `overlay`) |
| `init --project` | `qew init` | `project` lives on the `[[mirror]]` entry |
| `list --assignee` | `qew list --owner` | |
| `list --closed` | `qew list --state` | `closed` or `completed` |
| `list --epic` | `qew list --parent` | |
| `pr --for ITEM` | `qew pr open` | the item is the argument |
| `project ensure --title`, `project prune --title`, `project status --title` | `qew project ensure --target` | the board name is `project_title` on the mirror |
| `set --add-area`, `--remove-area` | `qew set --add-label`, `qew set --remove-label` | |
| `start --priority` | `qew set --priority` | priority is an item field, not a claim option |

## Flags dropped by design
- `--repo` on every command: the target is `.qew/config.toml`, not a flag.
- `--json` on write commands: writes print one `ID verb` line; machine output is on the reads (`show`, `list`, `ready`,
  `blocked`, `board`, `status`, `check`, `export`, `inbox`, `prime`).
- `hook --agent` is kept on the event commands (`qew hook session-start --agent`, `qew hook pre-tool --agent`, `qew hook post-tool --agent`).
