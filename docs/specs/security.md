# Security boundaries

## Issue content

- Overlay mode performs server-side `qew:managed` filtering before routine
  issue collections or Project projection reach Qew. Repository mode is an
  explicit grant to treat the whole issue tracker as intake.
- `qew:managed` grants graph membership only. `qew:approved` remains the
  independent trust gate for routine agent context.
- Titles, bodies, comments, branch names, and user names are untrusted data.
- Routine commands may read only bounded metadata from approved Qew work.
- Bodies and comments require an explicit inspect or triage action and are never
  fetched or emitted by `ready` or `prime`.
- `prime` locally revalidates approval, quotes and byte-bounds titles, withholds
  titles carrying suspicious Unicode findings, and emits explicit data-is-not-
  instructions guidance.
- `list --bodies` is an explicit body-inspection action, but remains restricted
  to server-filtered and locally revalidated approved work.
- `show ISSUE` and `triage` are the only reads that may expose unapproved issue
  text. Search terms are passed as a quoted GraphQL variable, never interpolated
  into the GraphQL document.
- Explicit reads annotate zero-width characters, bidirectional controls,
  Unicode tags, and high-signal ASCII confusables without rewriting the text.
  Confusable and emoji-exception behavior is pinned to reviewed Unicode 17.0
  source snapshots rather than the host Python Unicode database. Tag
  sequences are exempt only when they spell a recommended subdivision flag
  (England, Scotland, Wales); all other tag text is reported.
- Project reconciliation reads identifiers, state, labels, assignments,
  dependencies, and pull-request state; it does not fetch issue titles, bodies,
  comments, or other instruction-shaped text.
- Lane claim markers live in comments, which anyone may write. Only markers
  authored by a current assignee count, their JSON must have exactly the
  documented keys (`kind` optional), and only validated lane, workspace and
  kind values are emitted. A marker that fails validation is skipped with a
  warning, never trusted and never fatal. `--workspace` publishes a local
  absolute path (often under a home directory) in an issue comment; pass it
  only when that path may be shown to everyone who can read the issue.
  Marker JSON escapes `<` and `>` so free text cannot end the HTML comment.
- Approval requires configured labels plus Qew provenance or explicit human
  triage; author identity alone is insufficient.

## Execution

- Invoke `gh` with an argument vector, never a shell command string.
- Parse structured JSON and reject missing, unexpected, or truncated graph data.
- Never execute text obtained from GitHub or interpolate it into paths.
- Keep read and mutation credentials separate where the host supports it.
- A tracker credential must not grant code push or merge authority.

## Supply chain

- Start with the Python standard library and the existing `gh` executable.
- Add dependencies only through a trusted package index, with review and locked
  versions.
- Release artifacts must be reproducible or carry independent provenance before
  agents install them automatically.
