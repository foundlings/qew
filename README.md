<img src="assets/qew-icon.png" alt="Qew" width="96">

# Qew

Offline-first, file-based work ledger with dependency-aware readiness, mirrored to GitHub, Forgejo/Gitea and
GitLab. The ledger is the source of truth; forges are views. A forge being down never blocks work.

- Items, dependencies, state and evidence are plain files in a ledger repository (any git or jj repo).
  Every write adds a file or edits one item's own file, so concurrent branches merge cleanly.
- Readiness, blocked work and the dependency graph are derived from files: no network, no tokens spent.
- `qew complete` is gated: claimed, children done, and evidence whose changes are ancestors of the target branch.
- Only a human's quoted words release a draft to approved work.
- `qew sync` mirrors to any number of repositories across forges; each item records every mirror.
- Claude, Codex, Cursor and opencode hooks prime sessions and refuse forge-side bypasses.

## Install

Python 3.11+, no runtime dependencies. `python3 scripts/setup_user.py` installs `qew` into `~/.local/bin`.

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
items/<ID>.md                   fixed header ended by `---`, then a Markdown body
events/<ID>/<EVENT-ID>.json     append-only facts; state is their fold
mirrors/<ID>/<target>.json      one file per (item, mirror target)
boards/<target>.json            board recorded by `qew project ensure`
```

Header keys: `id kind title parent needs labels owner priority created`. Kinds: epic, issue, task. States:
draft, approved, claimed, completed, closed. IDs come from `id_mode` per kind: `time` (default, 9 chars),
`letters` (A, B .. AA) or `counter`; imports keep their IDs. Details: `docs/specs/ledger.md`.

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
select = "label:area:erp"       # optional: label:X kind:X state:X priority:N; spaces = and, commas = or
```

Tokens come from `GITHUB_TOKEN`, `FORGEJO_TOKEN`/`GITEA_TOKEN`, `GITLAB_TOKEN` or the mirror's `token_env`.
`api = "URL"` overrides the API root for self-hosted installs. `qew sync --dry-run` shows the plan offline.

## Commands

Run `qew --help` for the workflow groups and `qew COMMAND --help` for flags; the full contract is
`docs/specs/commands.md`.

- Read: `qew show`, `qew list`, `qew ready`, `qew blocked`, `qew graph`, `qew check`
- Work: `qew create`, `qew set`, `qew start`, `qew release`, `qew evidence`, `qew complete`, `qew close`, `qew reopen`
- Drafts: `qew draft` (group), `qew draft list`, `qew draft approve`, `qew draft revert`
- Pull requests: `qew pr` (group), `qew pr open`, `qew pr ready`, `qew pr merge`
- Mirrors: `qew sync`, `qew status`, `qew accept`, `qew reject`, `qew import`, `qew restore`, `qew project` (group), `qew project ensure`
- Setup: `qew init`, `qew hook install`, `qew hook remove`

Hook events agents call: `qew hook` (group), `qew hook session-start`, `qew hook pre-tool`, `qew hook post-tool` (each with `--agent`).

Flags (all documented in `--help`): `--ledger`, `--kind`, `--title`, `--parent`, `--needs`, `--label`, `--labels`,
`--priority`, `--body`, `--body-file`, `--quote`, `--from-file`, `--import`, `--json`, `--state`, `--change`, `--vcs`,
`--reason`, `--tree`, `--closed`, `--target`, `--item`, `--dry-run`, `--head`, `--base`, `--ready`, `--claude`, `--codex`,
`--cursor`, `--opencode`, `--agent`, `--completed`, `--from`, `--to`, `--forge`, `--repo`, `--api`, `--history`.

## Hooks

`qew hook install|remove --json` prints a versioned result for scripts. `qew hook install` writes project-relative config for Claude (`.claude/settings.json`), Codex
(`.codex/hooks.json`), Cursor and opencode. Session start prints a bounded list of ready work; before a shell
tool call, forge-side issue/label/board edits and pull request merges that bypass qew are refused. See
`docs/specs/hooks.md`.

## Tests

`python3 -m unittest discover -s tests`: command-level walks, offline; forge backends are tested over real HTTP
against local fakes. This is the gate; qew has no GitHub workflows.

Licensed under Apache-2.0.
