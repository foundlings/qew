<p align="center">
  <img src="assets/qew-icon.png" alt="Qew mascot: a silver-haired catgirl with a :3 expression holding a cigarette" width="160" />
</p>

# Qew

Qew turns GitHub Issues into a dependency-aware work queue for humans and
coding agents. GitHub remains the visible system of record; Qew derives ready
work from the issue graph and applies concise plan specifications.

```text
Milestone / campaign
└── Parent plan issue
    ├── Work-package issue ── pull request
    ├── Work-package issue ── pull request
    └── Work-package issue ── pull request
```

Work packages may depend on work in any plan. Newly discovered infrastructure
is recorded as work, linked to where it was discovered, and may block the
discovering work. Blocking relationships must remain acyclic.

## Commands

`--repo OWNER/REPO` and `--json` may precede the command or appear where shown.
Without `--repo`, Qew discovers the repository from the current checkout. JSON
list output is NDJSON so each line remains independently parseable. Commands run
under the tracked `.qew/config.json` policy of the current checkout.
`qew --help` lists every command grouped by workflow (read, work, drafts, pull
requests, plans, board, hooks), and `qew COMMAND --help` shows one command's
options. `qew --version` prints the installed version. Square brackets mark optional
arguments, `|` marks alternatives, and `...` marks repeatable values.

### Read and choose work

Load bounded work context for an agent. Run it explicitly (an agent skill can);
no hook runs it at session start, because that spends context in every session:

```text
qew prime [--repo OWNER/REPO] [--json]
```

List actionable work in priority order. Work labelled `needs:approval` is a gate a
human must authorize, so it is never listed as ready, whether or not it is also
claimed or blocked. The default limit is 30;
use `--limit 0` for the complete queue:

```text
qew ready [--repo OWNER/REPO] [--limit N] [--json]
```

Browse approved work. Add `--bodies`, `--label`, `--author`, `--assignee`, `--epic`,
`--state`, or `--closed` when needed. `--label` repeats and an issue must carry every
label given; `--author LOGIN` matches the `author:LOGIN` label Qew writes; `--assignee
LOGIN` matches assignees. Each `--json` row also carries its sorted `labels` and
`assignees`:

```text
qew list [--repo OWNER/REPO] [--label LABEL]... [--author LOGIN] [--assignee LOGIN]
         [--epic ISSUE] [--state open|closed|all | --closed] [--bodies] [--json]
```

See what needs one person's attention, in the selected repository, open issues only.
`LOGIN` defaults to the authenticated GitHub user and `--since` to seven days ago (UTC);
it takes `2026-10-07T09:00:00Z` or a date (`2026-10-07`, midnight UTC). Qew keeps no local
state. Three sections: **Replies** (issues involving LOGIN with a comment since TIME by
someone other than LOGIN, a bot, or Qew's own claim and release comments), **Waiting**
(`needs:approval` issues carrying `author:LOGIN`), and **Assigned**. The GitHub search is
read to its last page, and comment pages are read back to TIME; if either cannot be
covered (including GitHub's 1000-result search cap) the command fails with exit 4. Comment
text is shown only as data, first line cut to 100 characters. `--count` prints one line for
a session-start hook; `--json` prints NDJSON rows with a `section` of `reply`, `waiting`, or
`assigned` (`--count` and `--json` cannot be combined):

```text
qew inbox [--repo OWNER/REPO] [--login LOGIN] [--since TIME] [--count | --json]
```

Inspect one issue, including its body, relationships, and recent comments:

```text
qew show ISSUE [--repo OWNER/REPO] [--json]
```

Search vetted open and closed work before filing a duplicate:

```text
qew search TERM... [--repo OWNER/REPO] [--json]
```

Review managed issues with missing or contradictory type and priority labels:

```text
qew triage [--repo OWNER/REPO] [--search TERMS] [--json]
```

Explain which approved work is waiting and what blocks it:

```text
qew blocked [--repo OWNER/REPO] [--json]
```

### Create and edit work

Create typed, approved work. Repeat `--area`, `--blocked-by`, and `--done-when`
as needed; `--body-file` and `--edit` are long-form alternatives. `--quote`
names the human who asked for the work (`Name: 'their words'`); without it, or
with an empty, too-short, or placeholder quote, nothing is written (exit 2). The
quote is added to the body under `## Requested by`. New work carries
`needs:approval` unless `--approved` says the quote also authorizes starting it:

```text
qew create --type bug|enhancement|task --title TITLE --quote "NAME: 'WORDS'"
           [--approved]
           [--repo OWNER/REPO] [--priority P0|P1|P2|P3|P4]
           [--area AREA]... [--parent ISSUE] [--blocked-by ISSUE]...
           [--discovered-from ISSUE] [--needs-approval] [--where TEXT]
           [--problem TEXT | --goal TEXT] [--fix TEXT | --approach TEXT]
           [--done-when TEXT]... [--body-file FILE | --edit] [--json]
```

Every issue Qew opens carries `author:LOGIN`, the GitHub login of the `gh` user
who ran the command, so a board can show one person's work with
`label:qew:managed label:author:LOGIN`. Qew creates the label (grey, never
overwriting an existing one) the first time an author opens work in a
repository. It records who opened the work, not who works on it: an assignee
still means the issue is claimed.

Correct an issue's title, body, priority, type, area, or parent. Closed targets
require `--closed`. `--needs-approval` gates work that got stuck (claimed, in
review, or blocked included): its card moves to Needs approval at once and
`--no-needs-approval` restores the earlier column:

```text
qew set ISSUE [--repo OWNER/REPO] [--priority P0|P1|P2|P3|P4]
         [--type bug|enhancement|task] [--add-area AREA]...
         [--remove-area AREA]... [--parent ISSUE | --no-parent]
         [--needs-approval | --no-needs-approval]
         [--title TITLE] [--body-file FILE] [--closed] [--json]
```

Add a native, cycle-checked dependency edge:

```text
qew block ISSUE --on BLOCKER [--repo OWNER/REPO] [--closed] [--json]
```

Remove a native dependency edge:

```text
qew unblock ISSUE --from BLOCKER [--repo OWNER/REPO] [--closed] [--json]
```

### Drafts

A **draft** is an issue or plan that is filed and discussed but must not be worked
yet. Use drafts for anything a human has not approved: a plan being written, ideas,
a proposal. A draft carries the label `qew:draft` (created by `qew init`) and never
`qew:approved`, so Qew never offers it: it is not ready, not startable, not listed by
`ready`, `list`, `search` or `prime`, and not completable, even if someone also adds
`qew:approved` in the GitHub UI. Drafts can be epics, and an epic's children can be
drafts, so a whole plan stays held together.

```text
qew create --draft --type bug|enhancement|task --title TITLE --quote "NAME: 'WORDS'" [--parent N] ...
qew plan create --draft --title TITLE --quote "NAME: 'WORDS'" [--goal TEXT]
qew apply FILE --draft                                            # a whole JSONL plan as drafts
qew draft list [--parent N] [--json]
qew comment ISSUE (--body TEXT | --body-file FILE)
qew set ISSUE [--title T] [--body-file F] [--priority P] ...      # edit a draft like any issue
qew draft approve ISSUE... [--tree] --quote "NAME: 'WORDS'" [--json]
qew draft revert ISSUE... [--tree] --reason TEXT --quote "NAME: 'WORDS'" [--json]
```

- **Filing.** `--draft` contradicts `--approved` and `--needs-approval` (a draft is
  neither approved nor gated), a draft plan takes no `--children` (file them with
  `create --draft --parent N`), and `apply --draft` refuses a line with
  `"approved": true`. A repository needs `qew init` once so the label exists.
- **Releasing.** `draft approve` removes `qew:draft`, adds `qew:approved`, comments
  the human's quote and verifies each issue. An ISSUE that is not a draft is skipped
  and reported (exit 2 only when nothing is left to approve). `--tree` also approves
  every open draft below it, so a draft plan and all its children become ready
  together. A failure part-way exits 3 and names what was and was not approved;
  rerun the same command to finish, then run `qew project ensure`.
- **Taking back.** `draft revert` is the inverse: it returns approved work or a plan
  to draft (`needs:approval` is kept), comments the reason and quote, and refuses work
  claimed by anyone (plans included) or in review, so release or finish it first.
  With `--tree` one such descendant refuses the whole tree.
- **Pull requests.** A pull request for a draft issue (a plan's epic pull request,
  opened while the plan is still being written) needs no claim, is always a GitHub
  draft, is assigned to you and carries the label `qew:draft`. `qew pr ready` and
  `qew pr merge` refuse it until the issues it closes are approved: `draft approve`
  removes the label and `draft revert` puts it back.
- **Board.** Drafts sit in the Project's first column, `Draft`, with a `Drafts` view.
  `qew project ensure` turns an older board's `To triage` column into `Draft` in
  place, keeping its cards.
- **Reading.** `qew show` and `qew plan status` say when work is a draft.

### Work lifecycle and pull requests

Claim ready work. `--priority` may complete otherwise valid priority triage:

```text
qew start ISSUE [--repo OWNER/REPO] [--priority P0|P1|P2|P3|P4] [--lane ID [--workspace PATH] [--kind compute|token]] [--json]
```

Agent lanes that share one GitHub login name themselves with `--lane`. Qew
records the lane, and optionally where it works, as a claim comment, and refuses
a second lane's start on the same issue (exit 3, naming the holding lane), also
when that start omits `--lane`. The same lane starting again exits 5. When two
lanes race, the oldest claim marker wins, so both see the same winner.
`--kind token` marks a text-only lane (default `compute`). `qew show` reports
the current claim. The lane guards `start` and `release` only; it is good-faith
tooling for lanes sharing a login, not an access control.

Free a claim without closing the work, for example when a lane died. Qew
records who released it, the lane, and why. Name your lane with `--lane`;
another login's claim, or another lane's claim, needs `--force`, and a forced
release says so in its record:

```text
qew release ISSUE --reason TEXT [--lane ID] [--force] [--repo OWNER/REPO] [--json]
```

Complete claimed work. Completion needs proof that the work landed: before
any write, Qew asks GitHub whether `--commit` is already on the remote
`--landed-on` branch and refuses otherwise. The closing comment records the
full commit, the branch, and the merged pull requests that contain it. Work
with no commit says why with `--no-commit`, which is recorded the same way.
If a linked PR already closed the issue as completed, Qew clears the stale
claim and records the reason without reopening the issue. A closed issue with
no claim is refused:

```text
qew complete ISSUE --reason TEXT (--commit SHA --landed-on BRANCH | --no-commit REASON)
             [--take-over] [--repo OWNER/REPO] [--json]
```

Another login's claim is never touched by accident: `start` and `complete`
refuse it. When you fix someone's issue or finish their work, take the claim
deliberately. `qew start ISSUE --take-over --reason TEXT` frees their claim
(recorded like a forced release) and claims the work for you, after checking
the work may start; `qew complete ISSUE --take-over` finishes it, clears their
claim even on an issue a merged PR already closed, and records
`Taken over from LOGIN by YOU`. `--take-over` needs `--reason` on `start`, and
is a usage error when no other login holds the work.

Issues that `qew apply` created before it marked them managed carry `qew:approved`
and Qew's provenance marker but not `qew:managed`, so overlay mode ignores them,
and the label is reserved. Adopt marks exactly those (never pre-Qew issues):

```text
qew adopt [--author LOGIN] [--dry-run] [--repo OWNER/REPO] [--json]
```

Issues opened before Qew 1.8.0, or by an older Qew, have no `author:LOGIN` label,
and the label is reserved so it cannot be set by hand. Backfill adds it from the
opener GitHub records for each issue, and reports any it skipped:

```text
qew author backfill [--dry-run] [--repo OWNER/REPO] [--json]
```

Create a team label such as `epic:customer` so `--area` can apply it. Qew never
overwrites an existing label and refuses the names it reserves for its own state:

```text
qew area ensure NAME [--color HEX] [--description TEXT] [--repo OWNER/REPO] [--json]
```

After a pull request merges, GitHub closes its issue but leaves the claim and
a stale board card behind. Land the merge to clear them for your own claims,
using the merge commit as landing proof. Another login's claim is reported with
the `--take-over` command, never touched; an unmerged PR is a no-op:

```text
qew land PR [--repo OWNER/REPO] [--json]
```

Close work explicitly. `qew close` has no default: name what happened to the work.
Abandoned and duplicate work closes as not planned and duplicate; work somebody else
delivered, or a later issue replaced, closes as **completed** with its proof:

```text
qew close ISSUE --reason TEXT (--abandoned | --duplicate-of ISSUE |
         --delivered-by SHA --landed-on BRANCH | --superseded-by ISSUE)
         [--repo OWNER/REPO] [--json]
```

`--delivered-by` verifies the commit is on the remote branch exactly as `qew complete`
does (exit 2 when it is not), and records `Landed: commit ... on BRANCH` and its merged
pull requests in the closing comment. `--superseded-by` links the replacing issue, which
must exist. Both release any claim and complete a parent whose last child this was. A
close with no disposition is refused (exit 2), and so is `--completed`: both name
`qew complete` for work a lane claimed, so completed work always carries its landing record.

Reopen closed work and clear stale claim state:

```text
qew reopen ISSUE --reason TEXT [--repo OWNER/REPO] [--json]
```

Open a pull request for claimed work, assigned to you. It is a draft unless `--ready` is present;
structured fields or `--body-file` provide the body:

```text
qew pr [--for ISSUE] [--repo OWNER/REPO] [--title TITLE]
       [--what TEXT] [--why TEXT] [--testing TEXT] [--body-file FILE]
       [--base BRANCH] [--head BRANCH] [--ready] [--json]
```

Mark your draft pull request ready for review. It is unchanged when already ready,
refuses a merged or closed pull request and one another login opened (unless you hold
the claim on every issue it closes), and verifies the draft flag is gone:

```text
qew pr ready PR [--repo OWNER/REPO] [--json]
```

Tick checklist items of your open pull request in place (a `- [ ]` line outside a
code fence is an item; its id is its `<!-- pr-progress:ID -->` marker, else its
1-based position). Unknown or duplicate ids are exit 2, another login's pull
request exit 3 (unless you hold the claim on every issue it closes), and a body
edited meanwhile is never overwritten:

```text
qew pr check PR (ITEM... | --all | --list) [--repo OWNER/REPO] [--json]
```

Merge your ready pull request with a merge commit pinned to the checked head, then
land the issues it closed. It refuses a draft, conflicts, unticked checklist items,
a pull request closing a draft issue, failing checks, and pending checks (exit 2 or
3, nothing written). `--auto` needs required status checks on the base branch
(otherwise GitHub would merge at once) and prints the `qew land` command to run
afterwards; `--admin` bypasses branch protection, needs `--reason`, and skips only
the check rule. A teammate's pull request merges when you hold the claim on every
issue it closes (take it over deliberately with `qew start ISSUE --take-over
--reason TEXT`) or with `--other-author --reason TEXT`; neither overrides the
readiness rules:

```text
qew pr merge PR [--auto] [--admin] [--other-author] [--reason TEXT] [--repo OWNER/REPO] [--json]
```

### Plans and graph operations

Create an approved parent plan and optionally attach existing children. The
plan body records its requester quote; a plan is never started, so it never
carries `needs:approval`:

```text
qew plan create --title TITLE --quote "NAME: 'WORDS'" [--repo OWNER/REPO]
                [--priority P0|P1|P2|P3|P4] [--children ISSUE,ISSUE]
                [--goal TEXT] [--done-when TEXT]...
                [--body-file FILE | --edit] [--json]
```

Roll up all plans, or pass an issue number for one plan:

```text
qew plan status [ISSUE] [--repo OWNER/REPO] [--json]
```

Close a non-empty plan only after every child is closed:

```text
qew plan complete ISSUE --reason TEXT [--repo OWNER/REPO] [--json]
```

Check dependency cycles, vocabulary contradictions, and suspicious titles:

```text
qew graph check [--repo OWNER/REPO] [--json]
```

Validate a JSONL plan without writing. Remove `--dry-run` to apply it with a
resumable checkpoint; `--state` and `--throttle` override the defaults. Every
line carries its own `quote` (see `docs/specs/plan-files.md`):

```text
qew apply FILE [--repo OWNER/REPO] [--state FILE] [--dry-run] [--draft]
          [--throttle DURATION] [--json]
```

`--draft` creates every item and epic of the plan as a draft (see Drafts); release
the plan afterwards with `qew draft approve EPIC --tree`.

### Repository, campaigns, Projects, and hooks

Initialize repository policy and labels. The mode is always explicit. `--project`
links the checkout to its Qew Project by number, the stable one in the Project's
URL and in `project ensure` output, so renaming the board never unlinks it. The
link is stored in `.qew/config.json`, preserved by later `init` runs without
`--project`, and makes `--title` optional on `project ensure` and `project
status`. A `--title` that differs from the linked board's current title is
refused, and a linked number that no longer exists is an error, never a new
board:

```text
qew init --mode overlay|repository [--project NUMBER] [--repo OWNER/REPO] [--json]
```

Create or reconcile a milestone campaign and attach parent plans. Every
milestone description Qew writes ends with the requester quote:

```text
qew campaign create --title TITLE --quote "NAME: 'WORDS'"
                    [--description TEXT] [--due-on DATE]
                    [--plan ISSUE]... [--repo OWNER/REPO] [--json]
```

Show campaign counts and checked completion readiness:

```text
qew campaign status [--title TITLE] [--repo OWNER/REPO] [--json]
```

Close a non-empty campaign only when it has no open attached work:

```text
qew campaign complete --title TITLE [--repo OWNER/REPO] [--json]
```

Create or refresh the derived organization Project and its workflow views:

```text
qew project ensure [--title TITLE] [--repo OWNER/REPO] [--json]
```

Report Project drift without writing: issues missing from the Project or with a
stale Status or Priority, plus the command that fixes them. It also lists
closed issues that still carry a claim (an `in-progress` label or an assignee),
which a merge outside any agent leaves behind, and names `qew land` and
`qew complete` as the way to clear them:

```text
qew project status [--title TITLE] [--repo OWNER/REPO] [--json]
```

Remove another repository's cards from this board, for example after a run
named the wrong repository. Only that repository's cards are deleted, never the
board's own, and no issue changes:

```text
qew project prune --from OWNER/REPO [--title TITLE] [--repo OWNER/REPO] [--json]
```

Install Qew's shell-command hooks for one agent, or `all` of them. They make
Qew management automatic without spending any session-start context: after an
agent runs `gh pr merge N`, Qew runs `qew land N`; before an agent runs
`gh issue create|edit|close|reopen`, `gh pr create` or `gh pr merge`, Qew warns (never blocks)
and names the Qew command to use. Hooks stay silent outside a Qew repository,
never execute command text, and never fail the agent. Installing also removes
Qew's own earlier session-start `qew prime` entry:

```text
qew hooks install claude|codex|cursor|opencode|all [--json]
```

The hooks call `qew hook pre-shell|post-shell --agent claude|codex|cursor|opencode`,
which reads the agent's JSON payload on stdin, always exits 0 and is not meant to be
run by hand. Remove only Qew's owned hook entries:

```text
qew hooks remove claude|codex|cursor|opencode|all [--json]
```

Project status is derived from issue state, native blockers, claims, and linked
pull requests; the Project is never graph authority. When `.qew/config.json`
links a project (`qew init --project NUMBER`), `start`, `complete`, `close`,
`reopen`, `block`, `unblock`, `create`, `set`, `pr`, and `plan create` move the
affected cards themselves, reported as `project_sync` in `--json` output. A
failed sync only warns; it never fails the command. Without a linked project,
reconciliation is explicit: run `qew project ensure` when board freshness
matters, and `qew project status` to see what has drifted without writing.
Mutations that can stale an unlinked board print that command on stderr as a
hint, in both human and `--json` modes; JSON output also carries the same
signal as structured data.

Qew does not add issues to a Project. Either run `qew project ensure` or
configure the Project's auto-add workflow filtered on `label:qew:managed`. That
workflow can only be created in the GitHub UI. `--parent` creates native
sub-issues, so a board with "Auto-add sub-issues to project" enabled picks up
every child once the tracking issue is on the board.

## Upgrading to 2.0 (breaking)

The requester quote needs a major release (2.0.0); the release PR bumps the
version as usual. Callers must change before upgrading:

- `create`, `plan create`, `campaign create` and every `apply` line require a
  quoted human requester (`--quote "Name: 'their words'"`, apply field
  `quote`). Unquoted calls exit 2 before any write.
- New work packages carry `needs:approval` unless created with `--approved`
  (apply: `"approved": true`).
- Run `qew init` once so the repository has the `needs:approval` label;
  otherwise every default `create` fails because gh refuses an unknown label.
- Plan files written for 1.x (including `docs/plans/*.jsonl`) need a `quote`
  on every line before they can be applied again.

## Repository modes

`qew init` always requires an explicit mode and writes it to
`.qew/config.json`:

- `overlay` is the safe coexistence mode. Only issues carrying `qew:managed`
  belong to Qew's graph and dedicated Project; Qew-created work gets that label.
  Existing label definitions and unrelated issues or Projects are preserved.
- `repository` gives Qew the whole issue tracker. Every repository issue is
  intake and may appear in triage and the Project. Qew itself uses this mode.

The config may also set `minimum_version` (`MAJOR.MINOR.PATCH`). A Qew older than
it refuses every command with the required version and the upgrade command;
without the field nothing is checked. Qew ignores config fields it does not know,
so a newer field never makes a newer Qew fail, and `qew init` keeps them.

An optional `max_active_claims` (a positive integer) caps how many open claims
one login may hold in the repository: `qew start` refuses (exit 6) before
writing anything once the caller holds that many. It counts the caller's claims
only, because the config is shared by everyone who works in the repository, so
the same number applies to a person and to a fleet of lanes under one login.
The cap is approximate: starts racing from one login can each pass and briefly
exceed it. Claims left by dead lanes count until someone runs `qew release`.

`qew:approved` remains a separate trust decision: membership does not make
issue text trusted. Run `qew project ensure --title TITLE` with a dedicated
title when using overlay mode; Qew adds only managed issues to that board.
Other commands documented in `docs/specs/` are requirements, not claims about
implemented behavior.

## Building Qew with Qew

Qew v1 is driven by its own GitHub work graph:

- [Qew v1 milestone](https://github.com/887/qew/milestone/1)
- [Parent plan issue](https://github.com/887/qew/issues/1)
- [Qew Factory project](https://github.com/users/887/projects)
- [Implementation plan](docs/plans/qew-v1.md)

Run `qew ready --repo 887/qew` to select the next work
package from the dependency graph.

The prose design is paired with the directly applicable
[Qew v1 JSONL plan](docs/plans/qew-v1.jsonl).

Post-v1 work is tracked in the [Qew post-v1 milestone](https://github.com/887/qew/milestone/2),
[roadmap issue](https://github.com/887/qew/issues/52),
and [reviewed executable plan](docs/plans/qew-post-v1.md). It intentionally
excludes Beads migration.

## Design

- GitHub milestones represent campaigns.
- Parent issues represent plans; sub-issues represent work packages.
- Native `blocked by` relationships form the execution DAG.
- `discovered-from` is provenance, not an execution dependency.
- Unapproved issue content never enters routine agent context.
- Machine output is stable JSON; human output stays compact.
- `create` and `pr` preserve authored bodies while warning when command, flag,
  or explicit path tokens are not marked as Markdown code.

See [the model](docs/specs/model.md), [command contracts](docs/specs/commands.md),
[Project projection](docs/specs/projects.md), [agent hooks](docs/specs/hooks.md), and
[security boundaries](docs/specs/security.md). An opt-in
[least-authority auto-triage example](docs/examples/qew-auto-triage.md) keeps
issue content as data and limits writes to approved labels. The reproducible
[agent-output measurement](docs/measurements/agent-output.md) compares Qew's
machine context with equivalent canonical GitHub responses.

## Development

Requires Python 3.10+ and an authenticated GitHub CLI (`gh`). Qew has no runtime
Python dependencies initially. GitHub commands discover the repository from the
current checkout; `--repo OWNER/REPO` overrides discovery.

Install Qew into an isolated environment with
[pipx](https://pipx.pypa.io/stable/):

```sh
pipx install "git+https://github.com/887/qew.git@v2.10.0"
qew --help
```

On Linux and macOS, from a local clone, `scripts/setup_user.py` installs Qew into
`~/.local/share/qew/venv` and links `qew` into `~/.local/bin`:

```sh
python3 scripts/setup_user.py
```

It replaces only its own link, warns when `~/.local/bin` is not on `PATH`, and
refuses on other systems. Homebrew is not supported because its sandbox cannot
authenticate to the private enterprise host. See the [installation contract](docs/specs/installation.md).

Git must already be authenticated for the enterprise repository. From a local
clone, `pipx install .` installs the same package. Qew declares no Python runtime
dependencies; Python 3.10+ and the external `gh` executable are its complete
runtime requirements.

Published releases attach a wheel, source archive, SPDX 2.3 SBOM, and
`SHA256SUMS`. GitHub artifact attestations bind each distribution to its source
and workflow provenance and separately bind the SBOM. See the concise
[release contract](docs/specs/releases.md).

For development inside a disposable environment:

```sh
python3 -m pip install .
python3 -m unittest discover -s tests
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
