<img src="assets/qew-icon.png" alt="Qew" width="96">

# Qew

Offline-first, file-based work ledger with dependency-aware readiness, mirrored to GitHub, Forgejo/Gitea and GitLab
(any number of repositories across these forges at once). The ledger is the source of truth; forges are views. A forge
being down never blocks reading, planning or finishing work.

- Items, dependencies, state and evidence are plain files in a ledger repository (any git or jj repo).
  Every write adds a file or edits one item's own file, so concurrent branches merge cleanly.
- Readiness, blocked work and the dependency graph are derived from files: no network, no tokens spent.
- `qew complete` is gated: claimed, children done, and evidence whose changes are ancestors of the target branch.
- Only a human's quoted words release a draft to approved work.
- Replication: every write commits to the ledger, pushes it and syncs the forges in a background worker; checkouts on
  several machines converge, and a forge can be the single authority for who owns what.
- `qew sync` mirrors issues, labels (coloured; priorities `P0`..`P4`, plain `epic`/`task`, `author:NAME`), dependencies, pull requests and project boards to each forge, and pulls forge-side
  edits back (only what changed since the last pull, deletions included); a state change made on a forge is a
  divergence a human settles with `qew accept` / `qew reject`.
- Claude, Codex, Cursor and opencode hooks prime sessions and refuse forge-side bypasses.

## Install

Python 3.15+, no runtime dependencies (the type checker is a developer tool). On Linux and macOS `sh scripts/install.sh` installs `qew` into `~/.local/bin`
(details: `docs/specs/installation.md`).

## Quickstart

```
qew init --ledger ../myproject-ledger         # .qew/config.toml + qew-ledger.toml
qew create --kind issue --title "Parse headers"            # a draft
qew draft approve ID --quote "Alex: 'yes, build the header parser first'"
qew ready                                      # approved, unclaimed, unblocked
qew start ID
qew evidence ID                                # cites the current commit / jj change
qew complete ID                                # refused until the gate passes
qew check                                      # validate the ledger
```

## Ledger

```
qew-ledger.toml                 name, schema_version, id_mode
items/<ID>/item.md              fixed header ended by `---`, then a Markdown body
events/<ID>/<EVENT-ID>.json     append-only facts; state is their fold
mirrors/<ID>/<target>.json      one file per (item, mirror target)
boards/<target>.json            board recorded by `qew project ensure`
views/<name>.json               named offline board (`qew board create`)
```

Header keys: `id kind title parent needs tracks pr labels owner priority created aliases` (`tracks` and `pr` are optional
ledger ids of the tracking issue or pull request item, never forge numbers or URLs; `aliases` lists the ids the item had
before `qew migrate-ids`, and `qew show OLD` still resolves them). Kinds: epic, issue, task. States:
draft, approved, claimed, completed, closed. IDs come from `id_mode` per kind (defaults: epics `letters`, issues and tasks `base35-time`, 9 chars):
`letters` (A, B .. Z, AA .. ZZ, AAA, in strict order; `qew next-id` prints the next free ids) or `counter`; imports keep their IDs. `qew migrate-layout [--dry-run]` moves an older ledger straight to the latest layout (each item file into its own directory, items/ID/item.md) in one commit; `qew migrate-ids --to base35-time [--dry-run]` rewrites every
id that is not a base35-time id (and every reference to it) in one commit, keeping the old id as an alias. Details: `docs/specs/ledger.md`.

## Replication and authority

Ledger commands run in a write session: lock, fetch and merge the ledger remote, write, commit
(`qew: <verb> <ID> by <actor>`), push, then a detached worker syncs the forges. `QEW_OFFLINE=1` skips every network
step; the work is pushed and mirrored the next time you are online. `QEW_TRACE=1` prints every phase and forge call with its wall time on stderr. `[replication]` in `.qew/config.toml`
(`auto_commit`, `auto_push`, `auto_forge_sync`, `background_sync`, `fetch_interval_s`, `branch`, ...) tunes it;
`qew status` shows the authority, ledger and per-target state and any open divergences.

- **Primary forge.** `[authority] primary = "<target slug>"` makes that forge arbitrate claims, so two checkouts or agents
  never both own an item; `primary = "none"` (default) lets the ledger remote arbitrate. Every other mirror is read-only.
- **Mirror mode.** `mode = "repository"` (default) makes qew the tracker's owner; `"overlay"` shares a tracker with other
  tools and marks qew's issues with `qew:managed`.
- **Moving and copying.** `qew forge migrate --to TARGET` verifies a new forge and makes it primary; `qew ledger fork`
  creates an independent ledger under a new identity; `qew import` and `qew restore` rebuild a ledger from a forge and a
  forge from a ledger.
- **Boards.** `qew project ensure` builds the Status/Priority board and its eight views on the forge; `qew board` renders
  the same kanban offline.
- **Identity.** The actor comes from `QEW_ACTOR`, else the git or jj `user.name`; never the operating-system login.

Contracts: `docs/specs/ledger.md` and `docs/specs/forges.md`.

## Mirrors

`.qew/config.toml` in the code repo:

```
[ledger]
path = "../myproject-ledger"

[code]
target = "main"                 # branch evidence must be an ancestor of

[[mirror]]
forge = "github"                # github | forgejo (or gitea) | gitlab
repo = "https://github.com/acme/widgets"
project = 7                     # board where the forge has them
mode = "repository"             # default: qew owns the tracker. "overlay" = shared tracker: issues get qew:managed, views filter on it
select = "label:area:erp"       # optional: label:X kind:X state:X priority:N; spaces = and, commas = or
```

Tokens come from `GITHUB_TOKEN`, `FORGEJO_TOKEN`/`GITEA_TOKEN`, `GITLAB_TOKEN` or the mirror's `token_env`.
`QEW_FORGE_LOGIN` names the forge login pull requests are assigned to when it differs from `QEW_ACTOR`.
`api = "URL"` overrides the API root for self-hosted installs; `timeout = SECONDS` bounds every request (default 30).
Repo and API URLs must be http(s) without credentials, query or fragment, and `repo` names `owner/repo`. `qew sync --dry-run` shows the plan: it reads the forge listing (never writes), so it also shows a wiped or recreated repository's issues as `create` lines. A pull that finds none of two or more mirrored issues on a complete listing treats the repository as recreated: it forgets those mirrors, recreates the issues in the same sync (`recreated=N`), and records no divergences; a missing-issue divergence clears once the issue is back.
`sub_issues = false` turns off showing the ledger parent as the forge's sub-issue relation (default on; each call counts against the create pacing). `ledger_web_url` (default: the ledger checkout's `origin`) is the ledger repository's web address; an issue lists an item's attachments in its generated footer with links to the stored files there (never the bytes), or plain names when no address is known. `git_url` / `ledger_git_url` name the clone URLs `qew sync` pushes the code repo and the ledger repo to (git remote
`qew-<slug>`); `read_only = true` makes a target one-way.

## Commands

Run `qew --help` for the workflow groups and `qew COMMAND --help` for flags; the full contract is
`docs/specs/commands.md`.

- Read: `qew show`, `qew list`, `qew ready`, `qew blocked`, `qew graph`, `qew plan status`, `qew check`, `qew export`, `qew inbox`
- Board (offline kanban): `qew board`, `qew board columns`, `qew board list`, `qew board create`, `qew board set`
- Work: `qew create`, `qew next-id`, `qew item` (group), `qew item attach`, `qew item detach`, `qew item extract`, `qew item rename`, `qew item assign`, `qew item unassign`, `qew set`, `qew start`, `qew release`, `qew evidence`, `qew complete`, `qew close`, `qew reopen`, `qew block`, `qew unblock`
- Drafts: `qew draft` (group), `qew draft list`, `qew draft approve`, `qew draft revert`
- Pull requests: `qew pr` (group), `qew pr open` (adopts an orphan pull request already open on its head), `qew pr check`, `qew pr ready`, `qew pr close`, `qew pr merge` (deletes the merged head branch), `qew land` once one merged, and `qew branch` (group), `qew branch prune` (`--base`, `--dry-run`) to clear the backlog of merged branches
- Mirrors: `qew sync`, `qew mirror` (group), `qew mirror add`, `qew mirror remove`, `qew mirror list`, `qew forge` (group), `qew forge migrate`, `qew status`, `qew accept`, `qew reject`, `qew conflicts`, `qew resolve`, `qew unlock --stale`, `qew repair-closes`, `qew import` (a forge, or `qew import DIRECTORY` for an exported tree), `qew restore`, `qew project` (group), `qew project ensure`, `qew project status`, `qew project prune`
- Setup: `qew --version`, `qew init`, `qew ledger` (group), `qew ledger fork`, `qew hook install`, `qew hook remove`

Hook events agents call: `qew hook` (group), `qew hook session-start`, `qew hook pre-tool`, `qew hook post-tool` (each with `--agent`), `qew hook vcs-check`.

Flags (all documented in `--help`): `--ledger`, `--kind`, `--title`, `--parent`, `--needs`, `--label`, `--labels`,
`--priority`, `--body`, `--body-file`, `--quote`, `--from-file`, `--import`, `--json`, `--format`, `--chains`, `--state`, `--change`, `--vcs`,
`--reason`, `--where`, `--problem`, `--goal`, `--fix`, `--approach`, `--done-when`, `--edit`, `--draft`, `--discovered-from`, `--no-parent`, `--add-label`, `--remove-label`, `--on`, `--no-commit`, `--landed-on`, `--delivered-by`, `--of`, `--by`, `--note`, `--key` (`qew create`, `qew comment`: idempotency key), `--take-over`, `--offline` (`qew start`: provisional claim), `--lane`, `--workspace`, `--force`, `--tree`, `--closed`, `--stale` (`qew unlock`: a human-approved removal of a dead git lock), `--target`, `--item`, `--dry-run`, `--full` (`qew sync`: re-read the whole forge), `--prune-labels` (`qew sync`: delete unused managed labels), `--after` (`qew next-id`, `qew item rename`: a letter reserved outside the ledger), `--count` (`qew next-id`), `--head`, `--base`, `--ready`, `--claude`, `--codex`,
`--compact`, `--name`, `--columns`, `--sort`, `--group-by`, `--filter`, `--cursor`, `--opencode`, `--agent`, `--completed`, `--from`, `--to`, `--forge`, `--repo`, `--api`, `--history`, `--read-only`, `--tracks`, `--pr`, `--primary`, `--remove-old`.
Hook flags: `--git`, `--pre-push`, `--revisions`, `--message-file`.
Pull request flags: `--what`, `--why`, `--testing`, `--all`, `--list`, `--auto`, `--admin`, `--other-author`.

Read flags: `--limit`, `--owner`, `--work-state`, `--bodies`, `--search`, `--since`.

Session context: `qew prime` prints what the session-start hook prints, for agents without hooks.

Search: `qew search TERM...` finds approved work offline by id, title and label.

Review: `qew triage` lists drafts and items missing planning fields with their bodies and text findings; `qew check --json` adds advisory findings.

Board and admin flags: `--due` (`qew set`, an epic's milestone due date); `--from` (`qew project prune`); `--edit` (body in `$VISUAL`/`$EDITOR`), `--closed` (`qew set` on closed work); `--login`, `--count` (`qew inbox`); `--json` on `qew check` prints a stable report; `--author` (list by the item's first-event actor; sync mirrors it as `author:LOGIN`).

Edge edits: `qew block ITEM --on BLOCKER` and `qew unblock ITEM --from BLOCKER` change one need at a time; `qew set` takes `--add-label`, `--remove-label`, `--body-file` and `--kind`.

Files: `qew item attach ITEM PATH [--as NAME]` attaches a file, or a directory with its subdirectories, to an item (bytes are immutable files named by their hash under `items/ID/attachments/`, the names are events, so merges never conflict); `qew item attach --manifest FILE` attaches many files to many items in one commit (JSON Lines of `{item, source, name}`; a create plan entry can carry `attachments` too); `qew item detach ITEM NAME` detaches a name or a directory of names; `qew item extract ITEM DIR` writes the attached tree out as real files; `qew item assign ITEM LOGIN...` / `unassign` list further assignees beside the claim owner (events; synced both ways with the forge issue); `qew item rename ITEM [NEW]` moves an item to another id (default the next free one) and keeps the old id as an alias; `qew show` lists them and `qew check` validates them and `[text](attachment:NAME)` links in the body.

Discussion: `qew comment ITEM --body TEXT` (or `--body-file F`) appends a comment to the item's history and prints its id; `--reply-to COMMENT` threads a reply, `--edit COMMENT` and `--delete COMMENT` (author only unless `--force`) append edit and delete events that keep the history; `qew show` lists the thread and `qew check` validates it. `qew comment --manifest FILE` adds many comments to many items in one commit (JSON Lines of `{item, key, text, author, at, reply_to}`, idempotent by key; a create plan entry can carry `comments` too).

## Hooks

`qew hook install --git` adds a tracked pre-push hook and `qew hook vcs-check` refuses pushes of commits whose `Refs: ID` trailers cite items without evidence (jj pushes are checked by the agent guard). `qew hook install|remove --json` prints a versioned result for scripts. `qew hook install` writes project-relative config for Claude (`.claude/settings.json`), Codex
(`.codex/hooks.json`), Cursor and opencode. Session start prints a bounded list of ready work; before a shell
tool call, forge-side issue/label/board edits and pull request merges that bypass qew are refused. A deliberate exception
is spelled `QEW_ALLOW_FORGE_BYPASS=1` before that one command (set in the hook's environment it allows the session). See
`docs/specs/hooks.md`.

## Tests

`python3 scripts/gate.py` (parallel; `python3 -m unittest discover -s tests` is the serial equivalent): command-level walks, offline; forge backends are tested over real HTTP
against local fakes. This is the gate; qew has no GitHub workflows.

Licensed under Apache-2.0.
