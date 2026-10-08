# GitHub boundary

- `--repo OWNER/REPO` is optional; otherwise Qew resolves the current checkout
  through `gh repo view` and validates the returned identity. Owner and name
  must match `[A-Za-z0-9._-]+`; anything else (such as a leading `@`, which
  `gh api -F` treats as a local file) is refused with exit `2` before any `gh`
  call.
- Every process call uses an argument vector with prompting disabled. GitHub
  content is never interpreted as a command or path.
- GraphQL pagination is completed for issue/search lists and nested blocker,
  blocking, sub-issue, assignee, and label connections before domain code
  receives data. `show` deliberately bounds comments to the ten most recent.
- Repository-label inventory is fully paginated. `init` writes only drifted
  Qew-owned labels and rereads the complete inventory before reporting success.
- A missing `gh` binary or a `gh` call exceeding the timeout (120 seconds, or
  `QEW_GH_TIMEOUT_SECONDS`) fails with exit `3` and one stderr line, never a
  traceback.
- Invalid JSON, GraphQL errors, missing fields, invalid field types, and
  incomplete pagination fail closed with exit `4`.
- Reads and writes are separate adapter operations. The credential supplied to
  `gh` should grant only the repository issue and Project authority required by
  the invoked command.
- `gh api --paginate` follows the first `pageInfo` in a response. A paginated
  query therefore lists its connection's own `pageInfo` before `nodes`, ahead of
  any nested `pageInfo` used for truncation checks; otherwise pagination stops
  at 100 results and the read fails as incomplete.
