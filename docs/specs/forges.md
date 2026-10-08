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
`Draft`, `Ready`, `In progress`, `In review`, `Done` (`forge.COLUMNS`).
- GitHub: Projects v2 (GraphQL). `ensure_board` finds the project by the mirror's `project` number, or
  creates one (titled by the mirror option `project_title`, default the repo name) with a `Status`
  single-select field holding exactly these options; `place_on_board` adds the issue (by `node_id`) and sets Status.
- GitLab: issue board (REST) whose lists are the scoped labels `board::<Column>`; placing sets that label.
- Forgejo/Gitea: no stable board API: `ensure_board` raises `Unsupported`; the state labels sync adds are the view.

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
- A `Status` field whose options differ from the requested columns is rewritten with `updateProjectV2Field`; GitHub
  then drops the values of replaced options, so items are re-placed by the next sync. A matching field is left alone.
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
