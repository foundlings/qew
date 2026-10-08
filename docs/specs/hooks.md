# Agent hooks

Agent adapters call the shared hook-configuration foundation; they do not write
configuration directly.

- Paths are fixed project-relative component lists. Absolute paths, traversal,
  symlinked roots, directories, and files are refused.
- JSON adapters mutate only their owned structural node. Unknown keys and values
  survive install and removal; a no-op does not rewrite the file.
- File adapters use a unique Qew ownership marker. They never overwrite or
  remove an unmarked file.
- Writes use a same-directory temporary file, flush file content, atomically
  replace the target, and flush the directory. A failed replacement leaves the
  previous configuration intact and removes the temporary file.
- Removal never creates directories or files; a missing parent directory is a
  no-op.
- New configuration directories and files are private by default. Existing file
  modes are preserved.

Claude, Codex, Cursor, and opencode schemas belong to their individual adapters.
The foundation contains no agent-specific paths or hook vocabulary.

## Where they install

The root is the nearest ancestor holding `.git` or `.jj`, so `hooks install`
and `remove` work in plain, colocated, and Jujutsu-only checkouts and never need
a private `.git`. Outside any repository they fail with exit 2.

## What the hooks do

No hook runs at session start: priming every session spends context. Every
adapter wires two events around shell commands to `qew hook`, which reads the
agent's payload on stdin:

- Before a command, `qew hook pre-shell` warns (never blocks) when the command
  is `gh issue create|edit|close|reopen`, `gh pr create` or `gh pr merge` (any form,
  `--auto` included), naming the Qew
  command to use.
- After a command, `qew hook post-shell` runs `qew land N` for `gh pr merge N`.
  Merges that no agent ran (UI, auto-merge, merge queue) are caught by
  `project status` stale-claim reporting instead.

The hook is data-only about command text: it is tokenized and matched
structurally, never executed. It exits `0` on every input, prints nothing
outside a Qew repository, and a failed landing becomes a message naming
`qew land N`. `install` removes Qew's own earlier session-start entry and keeps
every unrelated entry; `remove` deletes exactly Qew's entries.

## Claude Code

- `hooks install claude` adds one `Bash`-matched group each to `PreToolUse`
  (10-second timeout) and `PostToolUse` (60 seconds) in the repository root's
  `.claude/settings.json`, running `qew hook ...` in exec form so no shell
  parses the command. Output is `hookSpecificOutput.additionalContext`.
- Malformed `hooks` or event nodes are refused without rewriting the file.

## Codex

- `hooks install codex` adds the same two `Bash`-matched groups to the repository
  root's `.codex/hooks.json` as fixed `qew hook ...` commands. Output uses the
  same `additionalContext` shape.
- Project hooks load only for trusted projects. Codex skips every new or changed
  non-managed hook until the user reviews and trusts its exact definition with
  `/hooks`; Qew reports this step after installation and cannot perform it.

## Cursor

- `hooks install cursor` adds `qew hook pre-shell --agent cursor` to
  `beforeShellExecution` and `qew hook post-shell --agent cursor` to
  `afterShellExecution` in the repository root's version-1 `.cursor/hooks.json`.
- The before hook always answers `permission: allow`, with `agent_message` when
  it warns. The after event cannot inject text for the model, so it only lands.
- Unsupported schema versions and malformed hook nodes are refused.

## opencode

- `hooks install opencode` owns `.opencode/plugins/qew-hooks.js`, a project
  plugin using only the JavaScript standard library. For `bash` tool calls it
  pipes the command to `qew hook` (fixed executable and arguments) in
  `tool.execute.before` and `tool.execute.after`, then appends the text to the
  tool output. It never throws.
- Install refuses to overwrite an unmarked file and removes Qew's own earlier
  `qew-prime.js` only when it carries Qew's marker.
