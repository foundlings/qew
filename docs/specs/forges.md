# Forge backends

A forge is a mirror of the ledger, never the source of truth. `src/qew/forge.py` is the one
interface; `forge_github.py`, `forge_forgejo.py` (Forgejo and Gitea) and `forge_gitlab.py` implement it.
Backends know nothing about ledgers: they turn `IssueSpec`, `PullRequestSpec` and board calls into REST
(and GitHub GraphQL) requests and parse the answers into `RemoteIssue` / `RemotePullRequest` / `Board`.

## Support priority
GitHub and Forgejo/Gitea are the first-class forges: every authority feature (claim arbitration on a primary forge,
read-only secondary mirrors, repo pushes, forge migration) gets full scenarios against `tests/fake_github.py` and
`tests/fake_forgejo.py`. GitLab is not extended: it keeps the behavior and the tests it has, and a new authority
feature needs no GitLab scenario. `tests/test_forge_priority_walk.py` holds the authority walks off the GitLab fake.

## Contract
- Built as `Backend(mirror, transport, env)`; `transport` is the injectable HTTP seam (`forge_http`).
- A missing capability raises `Unsupported(name)` (the base class default). Never fake a result.
- Failures are typed: network, timeout, 429, 5xx -> `ForgeUnavailable`; no token, 401/403 -> `ForgeAuth`;
  other 4xx -> `ForgeRejected(status)`. Forge text is untrusted data: never evaluate it, never use it as a path.
- Tokens come from the environment only (`forge_http.resolve_token`); never read from or written to files.
- `IssueSpec.labels` replaces the issue's label set. A backend that implements boards with labels (GitLab
  `board::<Column>`) must leave its own board labels untouched on `update_issue`.
- `find_issue(marker, recent=False)`: `recent` limits a scanning backend (GitHub) to the newest page, which is all the lookup after a write needs; Forgejo and GitLab search server-side and ignore it. Item syncs use it so their cost does not grow with the repository; a full `qew sync` scans everything.
- `create_issue` is not idempotent; the caller first calls `find_issue(marker)` (the marker is a whole line of the body: a marker merely quoted in text, blockquoted, recased or of another id never matches; pull requests are skipped; several matches resolve to the lowest number, which is how a race between two creators ends). Pull closes an open unmirrored twin and strips its marker: the higher number while the item is live, any number once it is finished.
- `merge_pull_request(number, expected_head_sha)` merges exactly that head (merge commit, no squash) and
  raises `ForgeRejected` if the head moved. `open_pull_request` honors `draft`.
- Branches: `list_branches()` (name, default, protected; a truncated listing raises), `branch_is_merged(name, base)`
  (the forge's own comparison says `base` holds the tip: GitHub `compare` `ahead_by == 0`, Forgejo `compare`
  `total_commits == 0`), `delete_branch(name)` (a branch already gone is success; a refusal raises) and
  `list_open_pull_heads()`. GitLab raises `Unsupported` for all four. Rules for what may be deleted: `branch_cleanup.py`.
- API roots: GitHub `https://api.github.com` (a GHES host uses `<host>/api/v3`; GraphQL `<host>/api/graphql`),
  Forgejo `<host>/api/v1`, GitLab `<host>/api/v4`. The mirror option `api = "URL"` overrides the root
  (self-hosted installs, and the fake servers in tests).
- Repo URLs name the project: `https://host/owner/repo`; GitLab namespaces may nest (`group/sub/repo`).

## Board columns
The eight Status values (`forge.COLUMNS`): `Draft`, `Backlog`, `Ready`, `Needs approval`, `Gated`, `In progress`,
`In review`, `Done`, derived by `sync_plan.column`, first match wins: completed or closed -> Done; draft -> Draft;
epic -> the subtree's column (below), else Backlog; label `needs:approval` -> Needs approval (outranks
claim, review and blockers); label `gated` -> Gated (work blocked on something outside the repository: keys, credentials, hardware, a live account; same precedence, next after needs:approval; added with `qew set ITEM --add-label gated --reason TEXT`, the reason kept as a comment `gated: TEXT`); open linked pull request mirror (recorded state not merged or closed) -> In review;
claimed -> In progress; the subtree's column (below); approved with every need completed -> Ready; else Backlog.
The subtree's column (`rollup.py`, display state only: the item's own state and claim never change) lets a mirrored epic or issue show the
ledger-only tasks below it, at any depth: any descendant claimed -> In progress; else open work that is all gated -> Gated (open work counted
at the leaves of what is still to do); else some descendant completed with open work left -> In progress. Sync recomputes it, and a child's
change marks its ancestors pending. Items always carry a kind and a
priority, so the "not triaged" Draft case cannot arise. `qew ready` never offers a `needs:approval` or `gated` item, so Ready on
the board is exactly what it lists. Colors and descriptions: `forge.STATUS_OPTIONS`; Priority P0-P4: `forge.PRIORITY_OPTIONS`.
- GitHub: Projects v2 (GraphQL). `ensure_board` finds the project by the mirror's `project` number, or
  creates one (titled by the mirror option `project_title`, default the repo name) with a `Status`
  single-select field holding these options (with colors and descriptions), a `Priority` field (P0-P4) and the views in `forge.BOARD_VIEWS`; `place_on_board` adds the issue (by `node_id`) and sets Status.
- GitLab: issue board (REST) whose lists are the scoped labels `board::<Column>`; placing sets that label and the scoped `priority::P0`-`P4` label, and `update_issue` leaves both scopes alone.
- Forgejo/Gitea: no stable board API: `ensure_board` raises `Unsupported`; the labels sync adds are the view (`qew:<state>`, `status:<Status>` carrying the eight-status model, `priority:N`, `kind:K`, `qew:managed`).

## Rate limits
A 429, or a 403 carrying `Retry-After` or an exhausted `X-RateLimit-Reset` (or naming a rate limit), is
`ForgeUnavailable` with `retry_after` seconds (60 when the forge names none, capped at an hour); a plain 403 stays
`ForgeAuth`. GraphQL `RATE_LIMITED` answers carry the same wait. A sync pass stops at the first one and reports
`retry after Ns`; the background worker writes `retry_at=EPOCH` into `.qew/sync.pending`, `queue` and
`retry_if_pending` start no worker before it, and `qew status` prints `sync retry_after=N` while it holds. The GitHub
backend reads a project's structure once per instance and caches issue node ids; sync reads the whole board with one
listing (`read_board_placements`) instead of a call per card, so an unchanged board costs a constant number of calls.

## Board projection
`place_on_board(board, number, column, priority)` sets Status and (where the board has one) Priority; `read_placement`
reads them back (None = not on the board); `remove_from_board` drops only the card. Sync reads before writing (drift is
repaired), reads back `sync.READBACK_ATTEMPTS` times after a write, and records `column`/`priority` in the mirror file only
once the forge shows them. A forge without `read_placement` falls back to the recorded column.
GitHub sets Status and Priority in one request (`SetCard`). `board_layout(board)` hands the sync layer the project
structure the backend read (opaque JSON, cached in `.qew/state/TARGET/board-layout.json`); `adopt_board_layout` starts a
later process from it, and a refusal of a cached id makes the backend re-read the structure once and place again. A card
for an issue created a moment ago is placed without reading it first; the read-back after placing stays.

## Tests (offline, no network)
Each backend ships a stdlib `http.server` fake of the forge's real wire format on 127.0.0.1 in
`tests/fake_<forge>.py` (started in a thread, state in memory, optional failure injection) and a walk
`tests/test_forge_<forge>_walk.py` that drives the backend through the real `urllib_transport` against it,
with the mirror option `api` pointing at the fake. Cover: create/find/update/read round trip, close with a
reason, labels replace, dependency link, board place (where supported), pull request open/ready/merge with a
moved-head refusal, auth refusal, 5xx -> `ForgeUnavailable`, and `Unsupported` where the forge lacks it.

## GitHub notes
- `Board.id` is the Projects v2 number as a string (`"7"`); it is accepted back as the mirror's `project`. Without
  `project`, an existing project with the mirror's title (`project_title`, default the repo name) is reused, never duplicated.
  Projects are looked up through the repository owner (user or organization).
- Options are reconciled by name through `updateProjectV2Field` sending existing option ids, so cards stay. A legacy
  name (`To triage`) is renamed in place to `Draft`, or dropped when `Draft` exists; an option outside the model is kept
  after ours and reported. A project created in the same call has only stock options, which are replaced. A matching
  field is left alone.
- Views are matched by name; the untouched default `View 1` becomes `Kanban`. A view whose layout or filter differs is
  updated (`updateProjectV2View`) and reported in `Board.notes`; views outside the model are never touched.
- `find_issue` scans the repository issue list (all states, pull requests skipped) for the marker; a listing longer
  than `MAX_PAGES` pages, or a `Link` that leaves the API root, raises `ForgeRejected` rather than truncating or following.
- `link_dependency` uses the issue dependencies REST endpoints (`blocked_by`), listing first so repeats are no-ops.
- Pull request `checks` folds the combined commit status (ignored when it has no statuses) with all check runs:
  any failure is `failing`, else any unfinished is `pending`, else `passing`; nothing at all is `none`.
- `merge_pull_request` re-reads the head and refuses locally when it moved, and also sends the expected `sha` to GitHub.
- A `null` issue body reads as empty; any other null or mistyped field raises `ForgeRejected`. GraphQL `errors`
  map `RATE_LIMITED` to `ForgeUnavailable`, `FORBIDDEN`/`INSUFFICIENT_SCOPES`/`UNAUTHORIZED` to `ForgeAuth`, else `ForgeRejected`.
- The token is resolved on the first request, not at construction.

## Forgejo notes
- One backend (`forge_forgejo.py`) serves Forgejo and Gitea; both mirror kinds map to it.
- Token is resolved per request, so a missing token raises `ForgeAuth` on first use (boards raise `Unsupported` without one).
- Labels are ids on the wire: names are resolved (and created, color `#ededed`) before each write, then the full set is replaced with `PUT /issues/{n}/labels`.
- There is no close reason on this API: `close_reason` is accepted and not sent.
- No draft field: a draft pull request has the title prefix `WIP:`; `mark_pull_request_ready` strips `WIP:` / `[WIP]`.
- `checks` come from the combined commit status of the head: no statuses -> `none`; `warning` is treated as `failing` (fail closed).
- Dependencies are listed first, so `link_dependency` is idempotent without relying on a 409.
- Pagination ends on `X-Total-Count`, an empty page, or a short page with no `rel="next"` Link; more than `MAX_PAGES` raises `ForgeRejected`.
- Merge sends `Do=merge` and `head_commit_id`; a moved head is the forge's 409, surfaced as `ForgeRejected(409)`.

## GitLab notes
- `RemoteIssue.number` is the project-scoped iid; `node_id` is the global issue id as a string.
- Labels travel as a comma-joined `labels` parameter, so a label containing a comma is `ForgeRejected(400)` before any request. Labels starting with `board::` are carried over on `update_issue`.
- `close_reason` is ignored: GitLab issues have no close reasons. A null `description` reads as "".
- `link_dependency` posts `link_type=is_blocked_by`; only HTTP 409 (already linked) counts as success.
- `Board.id` is the numeric board id as a string; `ensure_board` matches the mirror `project` option against a board id first, then a board name (default the repo name), and creates a board only when neither matches. It adds a list per `board::<Column>` label, creating labels as needed.
- `place_on_board` rejects a column not in `Board.columns` with `ForgeRejected(400)`; it writes nothing when the issue already carries exactly the target `board::*` label.
- Merge requests: `Draft:` title prefix (a leading `[Draft]`, `(Draft)` or `Draft:` is stripped on ready). `mergeable` is True for `detailed_merge_status` "mergeable", None while undecided (checking, unchecked, preparing, approvals_syncing, ci_still_running), False otherwise. Pipeline status maps to checks (success passing; failed, canceled failing; skipped none; others pending; an unknown status is `ForgeRejected`). A moved head yields GitLab's 409 as `ForgeRejected(409)`.
- Pagination follows `X-Next-Page` and raises `ForgeRejected` beyond `forge_http.MAX_PAGES` rather than truncating.
- The token is resolved per request, so constructing the backend never fails for a missing token.

## Reading a forge (import, pull, restore)
- `list_issues(since="")` returns every issue (open and closed, no pull requests) oldest first; a non-empty `since` (ISO instant) keeps only issues created or modified at or after it (GitHub and Forgejo `since`, GitLab `updated_after`) with title, body, labels,
  assignee and state; `list_comments(number)` returns user comments in creation order (system notes excluded);
  `create_comment(number, body)` posts one. Pagination is bounded by `MAX_PAGES`; a longer listing raises
  `ForgeRejected` instead of truncating. Malformed JSON raises `ForgeRejected`. The fakes in `tests/fake_*.py`
  must implement the same endpoints and honor failure injection (`down`, `fail`, `delay` where the fake has them).
  `RemoteIssue.comments` carries the forge's own user-comment count (GitHub and Forgejo `comments`, GitLab
  `user_notes_count`, which excludes system notes) in every issue result; 0 only when the forge reports none.
- Labels: before an issue write, a backend lists the labels the repository has and creates the missing ones with the description
  `Managed by qew` and a colour: `P0` d73a4a, `P1` fb8500, `P2` fbca04, `P3` 2da44e, `P4` 8b949e (the Priority field colours),
  `epic` 5319e7, `task` 006b75, `qew:` bfdadc, `status:` c2e0c6, `author:` d4c5f9, and for any other label (`area:*`,
  `drive`, ...) a fixed palette entry chosen by a checksum of the name, so every machine paints it the same. The same list
  repaints existing labels once per run: qew's own labels are held to the palette (colour and description); any other label
  is repainted only while it still has a default colour (`ededed`, empty, `000000`, GitLab's `428bca`) and keeps its description;
  a colour a human picked is never overwritten and no label is ever renamed or deleted here. A create that is not visible afterwards fails that item (`LabelNotPersisted`, reported against it).
- `read_board_cards(board)` lists every card (id, repository, issue number, Status, Priority) or raises `Unsupported`; a listing
  past the page cap raises instead of truncating. `qew.board_cards.local_cards` is the one reading rule: cards of another
  repository are ignored (a shared board's numbers collide), one card id seen twice is a pagination overlap, two ids for one
  issue is an error naming both (exit 1). `delete_card(board, card_id)` removes a card and never touches its issue.
- Milestones (opt-in per target, see commands.md `sync`): `find_milestone(title)`, `create_milestone`, `update_milestone`,
  `read_milestone`, `set_milestone_open` and `assign_milestone(issue, milestone)`; `RemoteIssue.milestone` is the milestone an
  issue sits in (0 none). GitHub and Forgejo use `/milestones` (`due_on`), GitLab project milestones (`due_date`,
  `state_event`). Dates are `YYYY-MM-DD` at this interface. A forge without them raises `Unsupported`.

## Incremental pull
A pull never lists the whole forge when it can avoid it. The fakes bump `updated_at` on every create, edit, comment and label change.
- **Conditional pages** (GitHub, Forgejo): `list_issue_pages(cached)` requests the creation-ordered issue pages, each with
  `If-None-Match` set to the cached page's ETag; a 304 (`forge_http.NotModified`, a result, never an error) reuses the
  cached page, a 200 replaces it. The union of the pages is the complete current set on every pull, so an issue missing from
  it is a deletion (an `exists` divergence) at the cost of the changed pages only; a page's ETag changes when any issue on it
  is created, edited, commented, deleted or shifts onto it. A last page as full as the others is followed by one more request.
  A 200 without an ETag makes the forge raise `Unsupported("conditional_listing")` and the pull falls back below.
- **Fallback** (no ETags, GitLab, the in-memory forge): `list_issues(since)` with `since` = the last successful pull's start
  minus 120 seconds. Only a full listing (no cursor, `qew sync --full`, or the last complete set older than 24 hours) may conclude an
  issue was deleted; an incremental result merges with the mirrored set by number.
- **Local state**, never committed: `.qew/state/TARGET/cursor.json` (`pulled_at`, `full_at`, `mode`) and
  `.qew/state/TARGET/pages/N.json` (ETag and parsed issues). Both change only after a pull whose requests all succeeded; damaged
  files cost one unconditional full listing. `qew status` shows the last pull, the last complete set and the next pull's mode.

## Crash orphans and twins
A create whose response was lost leaves an orphan; the sync keeps a local intent (`ledger.md`, Background sync) so the
next sync of that item searches every issue for its marker before creating. In pull the mirror decides which issue of an
item stays open: other open issues with its marker are closed (a lower-numbered one after a 10 minute grace), and an
item with no mirror adopts its lowest-numbered marker issue. GitLab and Forgejo search by marker on the server, so their
lookup was never limited to the newest issues.

## Forge login
The forge assignee is a person, not the agent that acts for them. `[forge] login = "887"` in `.qew/config.toml` is the login every claim assigns the mirrored
issue to (a release unassigns) and every `qew pr open` assigns the pull request to; `[forge.logins]` maps an actor to its own login; a mirror's
`assignees = { actor = "login" }` wins over both, and its key `*` is the default that `[forge] login` sets. Resolution: that map, then `[forge.logins]`,
then `[forge] login`, then (pull requests only) the actor; `QEW_FORGE_LOGIN` overrides a pull request's. The claim stays the ledger's `claimed` event (actor
`elf5`): the forge login is display only, a pull never imports it as an assignee change, and removing it on the forge is a divergence like any claim owner's.

## Status labels
The column also shows as a label, for an issue list without the board: `ready` (Ready), `in-progress` (In progress) and `gated` (Gated), added and
removed by sync from the same `sync_plan.column` that places the card, so label and column cannot disagree; any other column, and every finished or
closed item, carries none. They are display only: the ledger's own `gated` label stays the source of truth for the item's own gate and is shown only
while the item sits in Gated (a gated epic, issue or task always does); `ready` and `in-progress` are reserved ledger names. All three are managed
labels: a pull never imports them as a human's edit, and a hand-added or hand-removed one is put right by the next sync (no divergence, no event).

## Rate limits and locks
A call GitHub refuses as rate limited (a GraphQL `RATE_LIMITED` answer, or a REST 429 or 403 naming a rate limit) was not applied, so a
repeatable call (never a create: the pacer learns the pace from that block) waits and is sent again: the `Retry-After` the forge named, else 30 s
doubling per attempt with jitter, at most `forge_rate_retries` times (mirror option, default 6; env `QEW_RATE_RETRIES`), each wait capped by
`forge_rate_max_wait` (300 s in the background worker, 60 s otherwise). One worker's wait closes a gate that pauses every worker of the process,
and the sync summary says `rate limited N times; waited Ns`. A limit that outlasts the retries stops the target once with its retry time, as before.
A sync takes the ledger lock only to commit and yields to any verb waiting for it (a waiter leaves a marker beside the lock), so `qew pr ready`,
`start` and `complete` are never queued behind a long background sync.

## Board fields
A mirror's `project_fields = { "Board field" = "source" }` fills custom Projects v2 fields on each card. A source is `kind`, `priority`,
`state`, `field:NAME` (an item field's values joined by `, `) or `label:PREFIX` (the rest of the first label with that prefix). A text field
is set as given; a single-select field needs an option of that name. `qew project ensure` creates a text field for each mapped name the board
lacks (a field that exists, of any type, stays), so adding a field later is one `project_fields` entry and an ensure. A value a human changed on
the card is a divergence, as for any mirrored fact. Fields are not labels: once a label is replaced by a field the next sync removes it from the
issue, and `qew sync --prune-labels` deletes the definition once nothing carries it: the prefix is retired.

## Label pruning
`Forge` has three calls for `qew sync --prune-labels` (only labels qew retired: a prefix converted to a field, or its own namespace labels it no longer emits; never a label that is merely unused): `list_repo_labels()` (every definition), `labels_in_use()` (names carried by any
issue or pull request, open or closed; GitHub and Forgejo list pull requests among issues, GitLab reads issues and merge requests) and
`delete_label(name)` (a missing label is fine). Deleting a label also removes it from whatever carried it, so the command only deletes
names `labels_in_use()` did not return. A closed issue whose body carries `<!-- qew-duplicate -->` (a retired twin) does not count as carrying anything.
