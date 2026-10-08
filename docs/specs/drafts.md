# Draft work

Drafts are issues, plans (epics) included, that are filed and discussed but must
not be worked until a human releases them. They have their own board column,
never appear as ready, and a whole plan with its children is released in one
command. Command syntax is in `commands.md`; this file states the rules and the
trust boundary.

## Model

- A draft carries the label `qew:draft` and never `qew:approved` or
  `needs:approval`. The `qew:` prefix is reserved, so no area can collide with
  it; `qew init` creates the label.
- One canonical predicate, `NormalizedLabels.draft`, and `workable` (approved,
  not draft, not an epic). Every consumer uses it (`ready`, routine reads,
  `lifecycle` start and complete, `pull_request`, `prime`, the board projection,
  `epic_claims`, `plan_completion`, `campaign`), so an issue carrying
  `qew:draft` is a draft even if a human also added `qew:approved` in the UI.
- A draft is never ready, startable, completable or listed by `ready`, `list`,
  `search` or `prime`. `plan status` and an epic's rollup count it as `draft`;
  a draft plan is marked as such and never completable. A draft epic never
  auto-completes and never derives a claim; `campaign` refuses a draft plan.
- An open draft blocks what it blocks like any open issue (blockers are judged
  by state) and blocks `plan complete` of its parent.
- Approval authority is a human's quote (`Name: 'words'`), required exactly as
  for `create`. Qew records the quote; it cannot verify that the human said it,
  so this is no stronger than `create --approved`.
- Closing a draft is a discard (`qew close`) and is allowed.
- Draft is not `needs:approval`. A draft means nobody has decided to do the work;
  `needs:approval` means the work is decided but a named authorisation or answer
  is owed. `--draft` never combines with `--approved` or `--needs-approval`.

## Commands

- `create --draft`, `plan create --draft`, `apply FILE --draft`: file draft work.
  `plan create --draft` takes no `--children` (file them with
  `create --draft --parent N`); `apply --draft` refuses a line with
  `"approved": true`. A repository without the label fails at GitHub and the
  error names `qew init`.
- `draft list [--parent N]`: open drafts in scope with type and parent; honours
  overlay scope and reads `qew:draft` through its own paginated query.
- `draft approve N... [--tree] --quote Q`: remove `qew:draft`, add
  `qew:approved`, comment the quote, verify, per issue. `--tree` also approves
  every open draft descendant (each issue visited once; truncation or more than
  1000 issues is exit 4). An N that is not a draft is skipped and reported, so a
  rerun after a partial failure finishes the rest; exit 2 only when nothing is
  left. `needs:approval` is kept. A failure after the first write exits 3 and
  lists approved and remaining issues. The comment on each issue is the approval
  record. A comment that fails after its label move is a warning naming the
  exact `qew comment` to run.
- `draft revert N... [--tree] --reason TEXT --quote Q`: the inverse. Refuses,
  before any write, a closed or unmanaged issue, work claimed by anyone (plans
  included), and an issue with a ready (non-draft) open pull request. With
  `--tree` one such descendant refuses the whole tree. There is no force flag:
  pulling work from under a claimant is the accident Qew guards against, so use
  `qew release` first. Open issues blocked by a reverted issue stay blocked; a
  reverted plan stops deriving a claim.
- `comment N`, `set`, `show` (reports `draft`) work on drafts like any issue.
  `start`, `complete` and ordinary `pr` refuse a draft (exit 2, naming
  `qew draft approve`).

## Board

- The Status option `Draft` is the first column and holds everything not yet
  workable: drafts, and work missing approval, a type or a priority. A `Drafts`
  table view filters `status:Draft`.
- Projection: closed is `Done`; otherwise a draft (epic or not) is `Draft`,
  before every other rule.
- `project ensure` turns an old `To triage` option into `Draft` in place (same
  option id, same cards) and drops it when `Draft` already exists.

## Plans and their pull requests

A plan's epic pull request opens at planning time, while its integration issue
is still a draft. `qew pr --for ISSUE` supports that: no claim (a draft cannot
have one), always a GitHub draft (`--ready` is exit 2), labelled `qew:draft`,
assigned to its opener. The label means the plan is not approved: `pr ready` and
`pr merge` refuse it, `draft approve` removes it when none of the issues the
pull request closes is a draft, and `draft revert` puts it back. A label that
cannot be synced is a warning naming the `gh pr edit` to run.

## Pull request flow

- Every pull request Qew opens is assigned to its opener.
- Who may act on a pull request (`pr check`, `pr ready`, `pr merge`): its
  author, or a login assigned to every issue it closes (after a deliberate
  `qew start --take-over`); otherwise exit 3 naming the take-over. `pr merge`
  also accepts `--other-author --reason TEXT`.
- `pr check PR (ITEM... | --all | --list)`: an item is any `- [ ]` list line
  outside a code fence (CommonMark fences); its id is its
  `<!-- pr-progress:ID -->` marker, else its 1-based position (`@N` when a marker
  already uses N). An unknown or duplicate id is exit 2 before any write. The
  body is re-read just before the write and the write is refused (exit 3) if it
  changed; the window between that re-read and the write remains, because
  `gh pr edit` has no compare-and-set. The result is read back and verified.
  Qew edits the live body, never rebuilds it, so closing lines survive.
- `pr merge PR`: merge commit only, always `--match-head-commit`. Refuses a
  draft, a closed or merged pull request, a conflict, an open checklist box, a
  `qew:draft` label and a pull request closing a draft issue (exit 2). Without
  `--admin` or `--auto` it refuses unless the head commit's check rollup is
  SUCCESS or absent (a pull request pushed seconds ago has no rollup yet and is
  treated as having none). `--auto` needs required status checks on the base
  branch, from a branch protection rule or an active ruleset (otherwise GitHub
  would merge at once; rules that cannot be read are exit 4) and prints
  `qew land PR --repo R`. `--admin --reason` skips only the check rule and cannot
  combine with `--auto`. A direct merge is read once more after a second if
  GitHub still shows OPEN, verified merged, then lands as `qew land` does; if
  landing fails the exit is 3 and the exact `qew land` command is printed.
- The shell hook points `gh pr merge` (not `--disable-auto`) at `qew pr merge`.

## Help

`qew --help` groups every command by workflow, one line each; a test fails when a
command exists that no group lists.

## Out of scope

A prime line counting drafts (it would cost agent context on every session).
