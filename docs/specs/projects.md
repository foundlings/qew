# Campaigns and Project visibility

- A campaign is an open GitHub milestone. `campaign create` reconciles its
  description/due date and may assign approved parent plan issues (never other issues, and never moving a plan out of another campaign).
- `project ensure` projects every repository issue in repository mode, or only
  `qew:managed` issues in overlay mode, into a dedicated organization Project
  with canonical Status and Priority fields.
- Issue state, labels, native blocked-by edges, claims, and linked pull requests
  remain truth. Project fields are derived visibility and may be rebuilt.
- Overlay reconciliation never adds foreign repository issues and never removes
  or edits unrelated items already present in a Project.
- When repairing a Status or Priority field, Qew keeps options it does not own
  (with their ids, colors, and descriptions) after its canonical options. A
  malformed foreign option fails loudly rather than being dropped.

Status precedence is deterministic: closed → Done; `qew:draft` → Draft (plans and
epics included); plan/epic → In progress while it carries a derived claim, else Backlog;
unapproved or untriaged → Draft; labelled `needs:approval` → Needs approval; open pull
request → In review; claimed → In progress; zero open blockers → Ready;
otherwise → Backlog. The gate outranks review, claim and blockers so that stuck
work gated for a human shows where a human looks; clearing the label restores
the earlier column. A claim is never removed by gating.

An epic's claim is derived, not set by hand. While at least one open, non-epic
direct child (sub-issue) carries `in-progress`, the epic carries `in-progress`
and is assigned to exactly those children's claimants; when none does, Qew
removes the label and assignees it derived. Blocked-by edges, grandchildren, and
child epics never claim an epic. An epic with no claimed child and no
`in-progress` label is never touched, so a human owner survives. `start`,
`complete`, `release`, `close`, `reopen`, and `land` reconcile the parent epic
(a failure warns and never fails the command); `project ensure` reconciles every
open epic and reports `epic:#N`; `project status` lists `stale_epic_claims`,
which make the board out of sync.

The Project linked in `.qew/config.json` belongs to the checkout's own repository,
the one its `origin` remote names. Another `--repo` never uses it: `project ensure`
and `project status` then require `--title`, and mutations sync no card on the
checked-out repository's board. A checkout whose remote cannot be read keeps the link.

An organization board also holds other repositories' issues, whose numbers
collide with ours; a card for another repository's issue is ignored and never
stands in for a missing one. A board item listed twice with the same item id is a pagination overlap (cards
are being added) and is ignored; two different item ids for one issue are a real
duplicate and fail loudly (exit 4), naming both ids.

In overlay mode `project ensure` and `project status` also read the parent epic of
every managed issue they read, even when the epic itself is not managed, so its
derived claim and card follow the work; epics with no managed child stay out of
scope.

`epic` and `in-progress` are matched through Qew's normal label rules, which fold
ASCII case as GitHub does, so a repository whose epic label is spelled `EPIC` is
derived exactly like `epic`. When the last open child of an open, triaged epic
(exactly one priority) closes, the epic is completed through the same verified
completion as `plan complete`, with the reason `All N child issues are closed.`,
which also releases its claim; an epic with an open child, no children, or no
single priority is left open. A failure there only warns.

The default Kanban columns are Draft (everything not yet workable: drafts, and work
missing approval, a type or a priority), Backlog, Ready, Needs approval, In progress,
In review, and Done. A board that already has a hand-made Needs approval option keeps
it: Qew adopts the existing option by name and moves it behind Ready, preserving
option ids. An old `To triage` option becomes `Draft` in place, keeping its id and
cards, and is dropped when a `Draft` option already exists. Additional views are
Table, Ready, Blocked, Active, Review, Drafts, and
Roadmap. Priority slicing is useful but GitHub exposes it only through the view
UI, so it is not a correctness dependency of `project ensure`.

In overlay mode every Qew view filter is scoped to `label:qew:managed` (the
Kanban, Table, and Roadmap views carry that filter alone), so anything else that
reaches the board, such as an auto-added issue, stays out of Qew's views. Views
Qew does not own are never touched.

An apply-plan epic may name both a campaign and Project. Apply builds the issue
graph first, then assigns the plan to its campaign and reconciles the Project,
recording both identifiers in the plan checkpoint.

## Reconciliation cost

- Issue and dependency writes remain complete without a Project refresh; the
  issue graph is authoritative and Project status is derived visibility.
- Mutating commands never spend a complete Project reconciliation pass.
- When `.qew/config.json` links a project, `start`, `complete`, `close`,
  `reopen`, `block`, `unblock`, `create`, `set`, `pr`, and `plan create` move
  the affected cards: each issue is fetched with its Project items, a missing
  card is added, and only differing Status and Priority values are written.
  `complete`, `close`, and `reopen` also refresh the issues the target blocks.
  Overlay mode skips issues without `qew:managed`. The sync is targeted and
  unverified, and machine output reports it as `project_sync` instead of
  `project_reconciliation`.
- GitHub links a new pull request's closing reference asynchronously. After
  `pr`, the sync re-reads the issue with short bounded delays until the created
  PR appears among its closing references, so the card lands in In review. A
  reference that never appears is not guessed: the card keeps the derived
  status and a warning names `qew project ensure`. The waits (0.5, 1 and 2 s) are
  fixed; `QEW_LINK_RETRY_SCALE` (0 to 1) only shortens them, for tests.
- A failed sync warns on stderr and never fails the command, because the graph
  write already succeeded and a non-zero exit would invite retrying a
  non-idempotent command; the reconciliation hint then stays.
- Without a linked project, a successful mutation that may stale the projection
  should emit one bounded,
  actionable hint naming `qew project ensure --title TITLE`. Human output puts
  it on stderr; machine output carries `project_reconciliation.command` and
  `.reason` rather than extra prose.
- The caller decides when board freshness is worth the additional API calls.
  A hint never substitutes for required verification of the primary mutation.
- After Project writes, Qew retries only mismatched verification reads five
  times over a bounded 20-second delay window. Malformed reads fail immediately;
  a persistent mismatch still names each missing item or field and fails closed.
