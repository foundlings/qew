# Command contracts

`prime`, `ready`, `list`, `show`, `search`, `triage`, `blocked`, `graph check`, `start`,
`release`, `complete`, `land`, `adopt`, `area ensure`, `author backfill`, `project prune`, `close`, `reopen`, `create`, `set`, `block`, `unblock`, `apply`,
`pr`, `init`, `campaign create`, `project ensure`, `hooks install|remove`, and
`plan create|status|complete` and `campaign create|status|complete` are implemented.
Everything else below is planned and must not be exposed as a placeholder
command.

## Read and context

- `prime`: approved-metadata-only conventions, contradictions, and up to five
  ready, active, and blocked items per section. Text is at most 4096 UTF-8
  bytes; `--json` returns the same text as `additional_context` with its byte
  count. No installed hook runs it; callers such as the factory skill do.
- `ready [--limit N]`: approved, open, unclaimed, non-plan work with no open
  blockers, ordered P0 through P4. Work labelled `needs:approval` is a gate a
  human must authorize and is never ready; `prime` omits it from next ready and
  `start` refuses it (exit 2). The default limit is 30; zero returns all.
- `list [--label X]... [--author LOGIN] [--assignee LOGIN] [--epic N] [--state open|closed|all] [--bodies]`: approved,
  triaged work and plans, ordered ready, claimed, blocked, needs_approval, then plan; each item
  carries its derived work state, so the list query pages blockers and
  assignees like `ready` and refuses truncated pages. `--closed` aliases
  `--state closed`. `--label` repeats and every label must match; `--author`
  matches the `author:LOGIN` label; `--assignee` matches assignees. All three
  compare case-insensitively (ASCII), as GitHub does. Rows add `labels` and
  `assignees` (sorted).
- `inbox [--login LOGIN] [--since TIME] [--count | --json]`: open issues for one
  person. `LOGIN` defaults to the authenticated user; `TIME` is an ISO 8601 UTC
  instant or a date and defaults to seven days ago; a malformed value, a login that
  is not a GitHub login, or `--count` with `--json` is a usage error (exit 2). No
  local state. Sections: `reply` (search `involves:LOGIN updated:>=TIME`; issues with
  a comment at or after TIME whose author is not LOGIN, not a bot, and whose body
  carries no Qew claim/release marker), `waiting` (`needs:approval` and
  `author:LOGIN`), `assigned`. Searches page to the end and comments page back to
  TIME; an unpageable cursor, more than 1000 search results, or malformed data is
  indeterminate (exit 4). `--count` prints one line; `--json` rows carry
  `schema_version` and `section`.
- `show ISSUE`: the explicit numbered inspection boundary for body, labels,
  assignees, parent, children, dependencies, and ten most recent comments.
  Suspicious-text findings identify the title, body, or displayed comment and
  the displayed comment index. A plan also names its next priority-ordered
  ready child. A claimed issue reports its current lane claim (`claim`:
  lane, workspace, kind, and the login that wrote it, or null).
- `search TERMS`: GitHub best-match search constrained to approved issues;
  results are checked again locally for approval and complete vocabulary.
  Capped results warn; zero vetted matches name `triage --search` as the
  explicit unapproved-text continuation.
- `triage [--search TERMS]`: the explicit title/body inspection boundary for
  work missing or contradicting priority/type vocabulary. Plans require a
  priority but not a work-package type. Title and body findings remain
  field-local in both human and JSON output.
- `graph check`: report cycles, contradictions, and approved-title text findings.
- `blocked`: explain blockers for waiting work.
- `plan status [ISSUE]`: roll up children and identify the next ready child;
  without an issue, report every open epic carrying exactly one priority.

## Work lifecycle

- `create`: create typed work (`--type`) with priority, parent, blockers
  (`--blocked-by`), discovery origin (`--discovered-from`), and a structured
  (`--where`, `--problem` or `--goal`, `--fix` or `--approach`, `--done-when`),
  file-based (`--body-file`) or editor-authored (`--edit`) body. `--problem`/`--goal`
  and `--fix`/`--approach` are mutually exclusive, and so are the structured
  fields, `--body-file` and `--edit`. A requester quote is
  required (see Requester quotes). Editor commands are
  tokenized and executed directly, never through a shell. Every created issue
  carries `author:LOGIN` for the `gh` viewer, resolved once per command; a
  missing label is created without `--force`. A login that cannot form a label
  is indeterminate (exit 4). Authorship is never written as an assignee.
- `create --draft`, `plan create --draft`: file draft work or a draft plan
  (label `qew:draft`, never `qew:approved` or `needs:approval`); see `drafts.md`.
  `--draft` contradicts `--approved` and `--needs-approval` (exit 2), and a draft
  plan takes no `--children`. A repository without the label fails and the error
  names `qew init`.
- `draft list [--parent N]`: open drafts in scope (plans included) with type and
  parent; reads `qew:draft` through its own paginated query and honours overlay
  scope. `comment N (--body TEXT | --body-file FILE)` adds a comment to any
  issue. `show` reports `draft`.
- `draft approve ISSUE... [--tree] --quote Q`: remove `qew:draft` and add
  `qew:approved` per issue, comment the quote, and verify. Every ISSUE must be open
  (exit 2); one that is not a draft is skipped and reported, and only when
  nothing is left to approve is that exit 2. `--tree` also approves open draft
  descendants and skips the rest. A failure after the first write exits 3 naming
  approved and remaining issues; rerunning finishes them. Truncated sub-issues
  or more than 1000 issues is exit 4. `needs:approval` is kept.
- `pr check PR (ITEM... | --all | --list)`: tick checklist items (`- [ ]` lines
  outside code fences) of your open pull request in place. An item is addressed
  by its `<!-- pr-progress:ID -->` marker, else by its 1-based position (`@N` when
  a marker already uses N); fences follow CommonMark. Unknown
  items and duplicate ids are exit 2 before any write; another login's pull
  request is exit 3 unless the caller is assigned to every issue it closes (the
  lane that builds a plan holds its integration issue; take it over first); a body edited since the re-read just before the write is exit 3 and not
  overwritten (the window between that re-read and the write remains: `gh pr edit`
  has no compare-and-set); the
  result is read back and verified.
- `pr merge PR [--auto] [--admin --reason TEXT]`: merge your ready pull request
  with a merge commit pinned to the head that was checked, then land its closing
  issues. Refused before any write: not open (2), a draft (2), conflicts (2),
  unticked checklist items (2), a closing issue that is a draft (2), another
  login's pull request unless the caller is assigned to every issue it closes
  (after `qew start --take-over`) or passes `--other-author --reason TEXT` (3), failing checks (3), pending checks without `--auto`
  or `--admin` (3). `--auto` needs required status checks on the base branch (a branch protection rule
  or an active ruleset; rules that cannot be read are exit 4)
  (otherwise GitHub merges at once; exit 3) and prints the `qew land` command
  for afterwards. `--admin` bypasses the check rule only and prints its reason.
  `--auto` with `--admin`, or `--reason` without `--admin`, is exit 2. A pull
  request with no check rollup yet (just pushed) is treated as having no checks.
  The state is read once more after a second if GitHub still shows OPEN. If
  landing fails after the merge the exit is 3 and the `qew land` command is
  printed.
- `draft revert ISSUE... [--tree] --reason TEXT --quote Q`: return approved work
  or a plan to draft (`qew:approved` out, `qew:draft` in, `needs:approval` kept),
  comment the reason and quote, verify. Refused before any write: no reason or
  quote (2), closed or unmanaged (2), an open pull request (2), claimed by
  anyone, assignee or `in-progress`, plans included (3; release it first with
  `qew release`). With `--tree` open descendants are included, drafts skipped,
  and one claimed or in-review descendant refuses the whole tree. A target that
  is already a draft is skipped; exit 2 only when nothing is left to revert.
  Failure part-way exits 3 naming reverted and remaining issues; rerun finishes and
  `qew project ensure` refreshes the board. A comment that fails after its label
  move is a warning naming the exact `qew comment` to run (the labels are the
  truth). Skipped issues are named in the output.
- `pr` assigns the pull request it opens to its opener (`--assignee @me`), so a
  board and a reader see whose it is.
- `pr --for ISSUE` where ISSUE is a draft (a plan's integration issue): no claim
  is needed (a draft cannot have one), the pull request is always a GitHub draft
  (`--ready` is exit 2) and carries `qew:draft`. `pr ready` and `pr merge` refuse a
  pull request labelled `qew:draft` (exit 2). `draft approve` removes the label from
  open pull requests once none of the issues they close is a draft; `draft revert`
  adds it to the open draft pull requests of the reverted issues (a ready pull
  request still refuses the revert). A label that cannot be synced is a warning
  naming the `gh pr edit` to run.
- `apply --draft`: every item and epic the plan creates is a draft (`qew:draft`, no
  `qew:approved`, no `needs:approval`); a line with `"approved": true` is exit 2
  before any write. Release the plan with `qew draft approve EPIC --tree`.
- `set`: idempotently update title, non-empty file body, priority (`--priority`), type (`--type`), areas,
  or parent and verify the result. `--no-parent` removes hierarchy. Area
  options (`create --area`, `set --add-area/--remove-area`) refuse reserved Qew
  labels (`qew:*`, `author:*`, priority, type, `epic`, `in-progress`) with exit 2.
- `create` gates new work behind `needs:approval` unless `--approved` says the
  quote also authorizes starting it; `--approved` with `--needs-approval` is a
  usage error (exit 2). `create --needs-approval` states the gate explicitly: it files work a human must authorize before it starts
  and `set --needs-approval|--no-needs-approval` adds or clears that gate,
  verified after the write. Qew applies the `needs:approval` label, the card
  lands in Needs approval (never Ready) even when the work is claimed, in
  review, or blocked, and the label is reserved: it is refused as an `--area`.
  `list`, `plan status`, and the board report such work as `needs_approval`
  rather than claimed or blocked; clearing the label restores the earlier state. It is neither a dependency edge nor a question.
- `start --take-over --reason TEXT` and `complete --take-over`: deliberate
  takeover of work another login holds. Without the flag another login's claim
  is refused exactly as before (exit 3, naming the flag). `start` checks that
  the work may start (not blocked, not `needs:approval`), then frees the claim
  as a forced `release` recorded with the reason, and claims for the caller.
  `complete` proves the completion evidence first, then clears every assignee
  and `in-progress`, also on an issue GitHub already closed as completed, and
  appends `Taken over from LOGIN by CALLER` to the closing comment. `--take-over`
  without `--reason` (`start`) or when no other login holds the work (exit 2) is
  refused before any write.
- `start`: guarded claim with post-write verification. `--priority` completes
  triage only for approved work with one type and no priority; Qew re-reads
  after the priority write before claiming and reports partial state.
  `--lane ID [--workspace PATH] [--kind compute|token]` names the agent lane
  holding the claim, for lanes sharing one login. After the verified assignment
  Qew writes one claim comment carrying
  `<!-- qew:claim {"lane": ID, "workspace": PATH|null, "kind": KIND} -->`,
  re-reads every comment, and verifies the record. `kind` says what the lane
  does (`compute` builds and tests, the default; `token` is text only); a
  marker without `kind` reads as `compute`. The current lane is the **oldest**
  valid claim marker written by a current assignee after the latest valid
  release marker by a current assignee, so two lanes racing for one issue agree
  on the winner; the loser exits 3. A marker that does not validate is skipped
  with a warning on stderr and never blocks `show` or `start`. A start on work
  the caller already holds exits 3 naming the holding lane (or "without a
  lane"), also when `--lane` is omitted, and exits 5 only for the same lane or
  for a lane-less start on a lane-less claim. A lane is 1-64 of `A-Za-z0-9._-`,
  starting with a letter or digit, without `--`; a workspace is an absolute
  printable path without `--`. `--lane` is optional; `--workspace` and
  `--kind` require it. The lane guards `start` and `release` only:
  `complete`, `close`, `reopen` and `pr` do not check it (good-faith tooling for lanes sharing a
  login, not an access control). When `.qew/config.json` sets
  `max_active_claims`, `start` counts the caller's open claims after the
  availability checks and refuses at the cap (exit 6) before any write. The cap
  is approximate: the count is read before the claim, so starts racing from one
  login can each pass and briefly exceed it. Claims left by dead lanes count
  until someone runs `qew release`.
- `release ISSUE --reason TEXT [--lane ID] [--force]`: free a claim without
  closing the work, for recovering a stale or dead lane's claim. Open issues
  only; an issue with neither assignees nor `in-progress` is refused (exit 2)
  without writing. Lanes share one login, so the caller names its lane:
  releasing a claim held by another login, or a lane claim whose lane is not
  the one passed with `--lane`, is refused (exit 3) unless `--force`. A forced
  release says so in its comment. `--force` is a recorded acknowledgement that
  guards against accidents between lanes acting in good faith, not a permission
  check. A malformed claim marker never blocks a release (it is skipped with a
  warning). Qew removes every assignee and the `in-progress` label, then
  comments with the reason and
  `<!-- qew:release {"lane": ID|null, "by": LOGIN, "reason": TEXT} -->` naming
  the released lane, then re-reads and verifies no claim remains. A failed
  record after the release is reported as partial state.
- `complete`: close claimed work with a reason and completion evidence:
  exactly one of `--commit SHA --landed-on BRANCH` or `--no-commit REASON`
  (anything else is a usage error, exit 2, before any GitHub call). With a
  commit, Qew reads GitHub's comparison of the commit with the remote branch
  tip before any write: `ahead` or `identical` is landed; any other status, or
  a commit or branch GitHub cannot find, is refused (exit 2); malformed
  comparison or pull-request data, or a comparison of a different commit, is
  indeterminate (exit 4). The closing comment is the reason followed by
  `Landed: commit <full sha> on <branch>` and `PR: #N, ...` for the merged pull
  requests GitHub associates with the commit, or by `No commit: <reason>`. If
  GitHub already closed it as completed through a linked pull request, prove
  the evidence, clear the caller's stale assignment and `in-progress` label,
  then record the comment, without reopening. A closed issue with no claim to
  clear is refused (exit 3), never reported as done.
- `land PR`: after a pull request merges, complete the issues it closed. Closing
  issues come from GitHub's `closingIssuesReferences`, never from body text
  (truncated references are indeterminate, exit 4). Each closed issue still
  carrying the caller's claim is completed as `complete` does, with the merge
  commit on the base branch as landing proof, and its card is synced. An issue
  held by another login is reported with the exact `complete --take-over`
  command and never touched; an issue GitHub left open (a merge into a
  non-default branch) is reported and left alone. An unmerged PR or an issue
  with nothing to clear is a quiet no-op (exit 0), so `land` is idempotent and
  safe to call from a hook.
- `adopt [--author LOGIN] [--dry-run]`: add `qew:managed` to every issue (open or
  closed) that carries `qew:approved` and Qew's `qew apply` provenance marker
  (`<!-- qew:apply DIGEST:KEY -->`, 64 and 16 lowercase hex digits, alone on a
  line) but not `qew:managed`. `qew:` labels are reserved, so this is the only way
  to add the label to issues created before `apply` marked them. Issues without
  the marker (pre-Qew issues) or without `qew:approved` are never touched;
  `--author` limits it to one opener; `--dry-run` writes nothing; `qew:managed`
  not persisting is indeterminate (exit 4); repeating it finds nothing to do.
- `author backfill [--dry-run]`: add `author:LOGIN` to every in-scope issue (open
  or closed) that has no author label, taking the login from the opener GitHub
  records for that issue and never from the caller. In overlay mode only
  `qew:managed` issues are in scope. A missing label is created without
  `--force`; an issue that already has any `author:` label is untouched; an
  opener whose account no longer exists is skipped and reported; a login that
  cannot form a label, or labels that do not persist, are indeterminate (exit 4).
  `--dry-run` writes nothing. Repeating it finds nothing to do.
- Epics are derived from their children (see [projects](projects.md)): `start`,
  `complete`, `release`, `close`, `reopen`, and `land` reconcile the parent
  epic's claim, and closing its last open child completes the epic too;
  `project ensure` reconciles every open epic, and `plan complete` stays the
  explicit command.
- `area ensure NAME [--color HEX] [--description TEXT]`: create a missing team
  label so `--area` can apply it, without `gh`. It verifies the label exists
  afterwards (indeterminate, exit 4, if it does not persist), never overwrites an
  existing label (matched case-insensitively; reported `unchanged`), and refuses
  names Qew reserves (`qew:`, `author:`, priority, type, `epic`, `in-progress`,
  `needs:approval`) and malformed names, colours, and descriptions (exit 2)
  before any call. Defaults: colour `EDEDED`, description `Team label`.
- `close ISSUE --reason TEXT` with exactly one disposition: `--abandoned` (not planned),
  `--duplicate-of ISSUE`, `--delivered-by SHA --landed-on BRANCH` or `--superseded-by ISSUE`.
  The last two close as completed: delivered verifies the commit on the remote branch like
  `complete` and records it (and its merged pull requests) in the comment; superseded links
  an existing, different issue. No disposition, two dispositions, `--landed-on` without
  `--delivered-by` (or the reverse), and `--completed` are refused (exit 2) before any
  write; the refusals name `complete` for claimed work and `--delivered-by` for work others
  delivered. A claim is released and a parent whose last child closed is reconciled.
- `reopen`: comment, reopen, and release stale claim state.
- `block ISSUE --on BLOCKER` and `unblock ISSUE --from BLOCKER`: change native dependency edges after complete-graph
  cycle validation and verify the resulting edge state. `set`, `block`, and
  `unblock` refuse a closed target unless `--closed` is explicit.
- `pr`: create a linked draft or ready pull request (`--ready`; `--base` picks
  the target branch, `--what`, `--why` and `--testing` or `--body-file` supply
  the body) from work claimed only by the caller. The clean numbered branch must exactly match its upstream. Typed
  issue data supplies the default conventional title and What/Why sections;
  every body has exactly one closing reference and an optional parent trailer.
  Any closing keyword anywhere in the body (in-line, cross-repository, or URL
  form) that names other than the claimed issue is refused. In a Jujutsu
  checkout (`.jj/` found before `.git/`) the branch is the one bookmark at `@`
  (or at `@-` when `@` is empty), which must match `NAME@origin`; git is never
  run there. `--head BRANCH` skips derivation and trusts the caller, who has
  pushed it. A branch naming no issue is accepted with `--for`; one naming a
  different issue is refused with both numbers. A failed probe names its argv and
  exit status.
- `pr ready PR`: mark the caller's draft pull request ready for review. A merged
  or closed pull request is refused (exit 2) and one another login opened is
  refused (exit 3) unless the caller is assigned to every issue it closes, both
  before any write; an already-ready one is reported
  unchanged and writes nothing; a pull request still a draft afterwards is
  indeterminate (exit 4); a non-positive number is exit 2 before any call.
- `create` and `pr` warn on high-confidence command, long-flag, and explicit
  path tokens outside Markdown code spans. Warnings never rewrite or reject
  authored bodies.

## Requester quotes

- `create`, `plan create`, `campaign create`, and every `apply` line require a
  quote of the human who asked for the item: `--quote "Name: 'their words'"`
  (apply: a per-line `quote` field). The check runs before any GitHub call and
  before an editor opens; a refusal is exit 2.
- Refused: a missing or empty quote, one without a leading name and colon, an
  unquoted or multi-line quote, fewer than 12 characters of words, and
  placeholders (only dots or ellipses, `<...>`, TODO, TBD, placeholder,
  his/her/their words).
- Qew writes the quote, trimmed, at the end of the created issue body as
  `## Requested by` followed by a `> ` quote line, before any apply provenance
  marker.
- `qew init` creates the `needs:approval` label with the other Qew labels.
- Work packages created this way carry `needs:approval`, the separate "go"
  signal, unless `create --approved` or apply `"approved": true` says the quote
  also authorizes starting. Plans never carry it: they are never started, and
  `approved` on an apply epic is refused.
- `campaign create` records the quote in the milestone description whenever it
  writes one: at creation, and when `--description` drifts. Assigning plans to an
  existing milestone writes no new text; each plan already records its own
  requester. An apply campaign uses its epic line's quote.

## Plans and repository setup

- `plan create`: create an approved parent issue with a canonical structured
  (`--goal`, `--done-when`), file-based (`--body-file`) or editor-authored (`--edit`)
  body, then attach and verify optional existing
  children. Issue number lists accept ASCII digits only; anything else is a
  usage error (exit 2).
  A failed child attach reports the created plan, the attached
  children, and the remaining children so the caller does not create a second plan.
- `plan complete`: idempotently close a completed plan after verifying that it
  is non-empty and every child is closed, then point the caller to campaign
  status. Plan status names the checked completion command when it is ready.
- `campaign status`: show open and closed issue counts and name the completion
  command only for non-empty open milestones with no open work. Titles are
  untrusted: text output quotes and byte-bounds them, titles with suspicious
  Unicode findings are withheld (`title` null plus `text_findings`), and the
  command is omitted whenever the title would need rewriting.
- `campaign complete`: idempotently close a non-empty milestone after verifying
  that it has no open attached issues, then re-read the milestone.
- `campaign create`: idempotently create or reconcile an open milestone
  (`--title`, `--description`, `--due-on`) and optionally assign one or more
  numbered plan issues (`--plan`, repeatable), verifying each write.
  Every plan must be an open `epic` carrying `qew:approved` and must not
  already belong to another milestone; all plans are checked before any write.
  `changed` is true only when a milestone or assignment was actually written.
- `project ensure`: create or reconcile the organization Project, Status and
  Priority fields, repository issue projection, and standard workflow views.
- `project ensure|status [--title TITLE]`: the Project linked by number in
  `.qew/config.json` is used when present. `--title` may then only confirm that
  board's current title (a different value is refused, exit `2`), and a linked
  number that does not exist is refused without creating a board. Without a
  link, `--title` is required and `ensure` creates the board if it is missing.
- `project prune --from OWNER/REPO [--title TITLE]`: delete every card on the
  linked board whose issue belongs to `OWNER/REPO` (exact, case-insensitive),
  for removing cards another repository's run put there. `--from` must be
  `OWNER/REPO` and differ from `--repo`, else exit 2 before any call. It verifies
  none remain (exit 4 otherwise), reports the count removed, and never changes an
  issue. Repeating it removes nothing.
- `project status`: read-only drift report. It compares the
  Project's items with the derived projection and lists issues missing from the
  Project and issues whose Status or Priority is stale, with the exact
  `project ensure` command that fixes them. It writes nothing, exits `0` whether
  or not drift exists, and refuses a missing Project (exit `2`).
  It also lists `stale_claims`: closed issues that still carry an `in-progress`
  label or an assignee, left by a merge that never passed through an agent, with
  a `claim_fix` naming `land` and `complete`. Stale claims do not make the
  board out of sync; they are reported beside it.
- `apply FILE [--dry-run] [--throttle DURATION]`: validate and batch-create plans, work packages,
  parents, dependencies, campaigns, and Project projections from JSONL; real
  application waits 500ms between mutation operations by default, checkpoints
  each completed graph/campaign/project operation, and is resumable. Dry-run
  validates the duration but never sleeps.
- `init --mode overlay|repository [--project NUMBER]`: require and persist the repository authority
  policy, then reconcile P0-P4, bug/enhancement/task, epic, `qew:approved`,
  `qew:managed`, `needs:approval`, and `in-progress`. Overlay creates missing names but preserves
  existing definitions; repository mode reconciles Qew's definitions exactly.
- `hooks install|remove <claude|codex|cursor|opencode|all>`: preserve existing
  agent configuration while adding or removing Qew's shell-command hooks, and
  drop Qew's own earlier session-start `qew prime` entry. See
  [agent hooks](hooks.md).
- `hook pre-shell|post-shell --agent NAME`: what the installed hooks run. It
  reads the agent's JSON payload on stdin and always exits `0`. Outside a Qew
  repository it prints nothing. `post-shell` runs `land` for each
  `gh pr merge N` (a number or pull request URL; `--auto` and a bare branch are
  ignored). `pre-shell` prints a non-blocking warning naming the Qew command
  for `gh issue create|edit|close|reopen`, `gh pr create` and `gh pr merge`. Command text is
  tokenized and matched structurally, never executed or trusted.

Qew has no Beads migration command; it is greenfield.

The implemented `apply` creates issues, parents, dependency edges, campaigns,
and Project projections. Campaign/Project reconciliation reuses the same
idempotent commands used directly.

The implemented `graph check` reports graph cycles, closed-vocabulary
contradictions, and approved-title Unicode findings. `blocked` reports only
approved work with at least one currently open native blocker.

## Output and failures

- `--repo OWNER/REPO` and `--json` may precede the top-level command or appear
  in that command's documented trailing position. Conflicting repository values
  are refused. Local-only hooks reject `--repo`.
- The checkout's `.qew/config.json` describes the repository its `origin` remote
  names. For any other `--repo`, Qew runs in managed-only (overlay) mode, so only
  `qew:managed` issues are in scope, and ignores the checkout's board link and
  `max_active_claims`. A checkout whose remote cannot be read keeps its config.
- Without `--repo`, the repository comes from `gh`. In a Jujutsu workspace with
  no `.git`, Qew points `gh` at the real git store by following `.jj/repo` to
  `store/git_target`; if that cannot be followed it fails naming the workspace
  and suggests `--repo`. Plain and colocated checkouts are left to `gh`.
- `qew --version` prints `qew VERSION` and exits `0` without a command or any
  GitHub call.
- Mutations that can stale the derived Project print the `qew project ensure`
  hint on stderr in human and `--json` modes; JSON stdout stays a single
  parseable document that also carries it as `project_reconciliation`.
- Human output is bounded and contains no issue body by default.
- Every command supports versioned `--json`; list-shaped output is NDJSON when
  streaming makes partial results useful.
- Exit `0`: complete result; `2`: invalid request or graph; `3`: GitHub or
  authorization failure, including another caller's claim; `4`: indeterminate
  result such as truncation; `5`: work is already claimed by the current caller.
- Multi-step mutations checkpoint their completed GitHub identifiers and report
  partial completion; they never silently retry non-idempotent writes.

## Exit codes

- `0`: success.
- `2`: usage error, refused input or state (blocked, needs approval, not
  approved, untriaged), or invalid configuration; nothing written.
- `3`: conflict, failed verification, or a failed GitHub call (claimed by
  another lane or login, claim conflict, verification failed after a write,
  `gh` timeout or error).
- `4`: indeterminate GitHub data (malformed, truncated, or inconsistent).
- `5`: already claimed by you (same lane, or a lane-less claim).
- `6`: at the `max_active_claims` cap; release or finish a claim, then retry.
