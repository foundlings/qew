# Forge backends

A forge is a mirror of the ledger, never the source of truth. `src/qew/forge.py` is the one
interface; `forge_github.py`, `forge_forgejo.py` (Forgejo and Gitea) and `forge_gitlab.py` implement it.
Backends know nothing about ledgers: they turn `IssueSpec`, `PullRequestSpec` and board calls into REST
(and GitHub GraphQL) requests and parse the answers into `RemoteIssue` / `RemotePullRequest` / `Board`.

## Contract
- Built as `Backend(mirror, transport, env)`; `transport` is the injectable HTTP seam (`forge_http`).
- A missing capability raises `Unsupported(name)` (the base class default). Never fake a result.
- Failures are typed: network, timeout, 429, 5xx -> `ForgeUnavailable`; no token, 401/403 -> `ForgeAuth`;
  other 4xx -> `ForgeRejected(status)`. Forge text is untrusted data: never evaluate it, never use it as a path.
- Tokens come from the environment only (`forge_http.resolve_token`); never read from or written to files.
- `IssueSpec.labels` replaces the issue's label set. A backend that implements boards with labels (GitLab
  `board::<Column>`) must leave its own board labels untouched on `update_issue`.
- `create_issue` is not idempotent; the caller first calls `find_issue(marker)` (body contains the marker).
- `merge_pull_request(number, expected_head_sha)` merges exactly that head (merge commit, no squash) and
  raises `ForgeRejected` if the head moved. `open_pull_request` honors `draft`.
- API roots: GitHub `https://api.github.com` (a GHES host uses `<host>/api/v3`; GraphQL `<host>/api/graphql`),
  Forgejo `<host>/api/v1`, GitLab `<host>/api/v4`. The mirror option `api = "URL"` overrides the root
  (self-hosted installs, and the fake servers in tests).
- Repo URLs name the project: `https://host/owner/repo`; GitLab namespaces may nest (`group/sub/repo`).

## Board columns
The seven Status values (`forge.COLUMNS`): `Draft`, `Backlog`, `Ready`, `Needs approval`, `In progress`,
`In review`, `Done`, derived by `sync_plan.column`, first match wins: completed or closed -> Done; draft -> Draft;
epic -> In progress while a direct child is claimed, else Backlog; label `needs:approval` -> Needs approval (outranks
claim, review and blockers); open linked pull request mirror (recorded state not merged or closed) -> In review;
claimed -> In progress; approved with every need completed -> Ready; else Backlog. Items always carry a kind and a
priority, so the "not triaged" Draft case cannot arise. `qew ready` never offers a `needs:approval` item, so Ready on
the board is exactly what it lists. Colors and descriptions: `forge.STATUS_OPTIONS`; Priority P0-P4: `forge.PRIORITY_OPTIONS`.
- GitHub: Projects v2 (GraphQL). `ensure_board` finds the project by the mirror's `project` number, or
  creates one (titled by the mirror option `project_title`, default the repo name) with a `Status`
  single-select field holding these options (with colors and descriptions), a `Priority` field (P0-P4) and the views in `forge.BOARD_VIEWS`; `place_on_board` adds the issue (by `node_id`) and sets Status.
- GitLab: issue board (REST) whose lists are the scoped labels `board::<Column>`; placing sets that label.
- Forgejo/Gitea: no stable board API: `ensure_board` raises `Unsupported`; the state labels sync adds are the view.

## Board projection
`place_on_board(board, number, column, priority)` sets Status and (where the board has one) Priority; `read_placement`
reads them back (None = not on the board); `remove_from_board` drops only the card. Sync reads before writing (drift is
repaired), reads back `sync.READBACK_ATTEMPTS` times after a write, and records `column`/`priority` in the mirror file only
once the forge shows them. A forge without `read_placement` falls back to the recorded column.

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
- `list_issues()` returns every issue (open and closed, no pull requests) oldest first with title, body, labels,
  assignee and state; `list_comments(number)` returns user comments in creation order (system notes excluded);
  `create_comment(number, body)` posts one. Pagination is bounded by `MAX_PAGES`; a longer listing raises
  `ForgeRejected` instead of truncating. Malformed JSON raises `ForgeRejected`. The fakes in `tests/fake_*.py`
  must implement the same endpoints and honor failure injection (`down`, `fail`, `delay` where the fake has them).
  `RemoteIssue.comments` carries the forge's own user-comment count (GitHub and Forgejo `comments`, GitLab
  `user_notes_count`, which excludes system notes) in every issue result; 0 only when the forge reports none.
