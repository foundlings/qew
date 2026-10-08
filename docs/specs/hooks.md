# Agent hooks (v1)

`qew hook install|remove [--claude] [--codex] [--cursor] [--opencode]` (no flag = all four)
writes project-relative config at the nearest ancestor holding `.git` or `.jj`, through the
safe-write foundation described below (atomic, private, idempotent, foreign entries kept).
Outside a repository it fails with exit 2 and creates nothing.
`--json` prints `{schema_version: 1, agent, action: installed|removed|unchanged}` (Codex install adds
`trust_required: true`); with several agents the object carries an `agents` list in the fixed order
claude, codex, cursor, opencode. Human output is one `ACTION AGENT hook` line per agent, plus the Codex
trust line `review and trust it with /hooks before it will run` on install. Install drops Qew's own earlier entries
(`hook pre-shell|post-shell`, `qew prime`); remove deletes current and earlier Qew entries.

| Agent | File | Entries |
|---|---|---|
| Claude | `.claude/settings.json` | `SessionStart`; `PreToolUse` and `PostToolUse` matched `Bash` |
| Codex | `.codex/hooks.json` | the same three (trust each with `/hooks`) |
| Cursor | `.cursor/hooks.json` (version 1) | `sessionStart`, `beforeShellExecution`, `afterShellExecution` |
| opencode | `.opencode/plugins/qew-hooks.js` (marker-owned: a file without Qew's marker is never overwritten, install exits 2, nor deleted, remove says `unchanged`) | `bash` before/after; a refusal throws |

They run `qew hook session-start|pre-tool|post-tool --agent NAME`; the agent payload arrives on stdin.

- **session-start** prints bounded context: at most 10 ready items (priority order, short id,
  one line each, rows cut at 110 characters), the blocked and draft counts, and the verbs
  `ready start evidence complete show draft`. The whole text is at most 2000 bytes. Outside a
  Qew repository, or on an unreadable ledger, it prints nothing. opencode has no session-start
  entry (its plugin API has no context-injection point at session start).
- **pre-tool** refuses, only inside a Qew-managed repository (`.qew/config.toml` at or above the
  payload `cwd`), shell commands that bypass the ledger, naming the qew command to use. The command
  text is tokenized and matched structurally (`hook_guard.py`), never executed; quoted examples such
  as `echo "gh issue edit 3"` are one argument and pass; `sh -c STRING` is unwrapped.
  Refused: `gh issue create|edit|close|reopen|comment|delete|transfer|pin|unpin|lock|unlock`,
  `gh label` and `gh project` except `list`/`view`/`field-list`/`item-list`, `glab issue|label|milestone`
  writes, `tea` and `fj` issue, label, milestone and comment writes; `gh pr merge` (not
  `--disable-auto`), `glab mr merge`, `tea pulls merge`, `fj pr merge`; `gh|glab api` with a write
  method (explicit, or implied by `-f`/`-F`/`--input` without `-X GET`) on `issues`, `labels`,
  `milestones`, `boards`, GitHub `projects` or `pulls|merge_requests/N/merge` paths; GraphQL
  mutations touching issues, labels, projects, milestones, comments or `mergePullRequest`
  (a query taken from a file or stdin cannot be inspected and is refused); and the same write
  paths through `curl`, `wget` or httpie (`http`, `https`, `xh`). Reads stay allowed.
  Known gaps: wrappers taking a value (`sudo -u x`), `eval`, scripts from files.
- **post-tool** prints nothing except one line after `git push` or `jj git push` when the ledger
  holds claimed items owned by the current actor without evidence. Read-only.

## Exit codes and output

A hook exits 0 with agent-correct output except a deliberate refusal. This is the single place
qew swallows internal failures: inside `cmd_hook.run` for the three event commands, an
unexpected exception prints nothing and exits 0, because a hook must never break a session.
`install` and `remove` fail loudly as everywhere else.

Verified against each tool's documentation (October 2026):

- Claude Code: `SessionStart` output `{"hookSpecificOutput": {"hookEventName": "SessionStart",
  "additionalContext": TEXT}}`; `PreToolUse` stdin has `tool_name`, `tool_input.command`, `cwd`;
  deny is `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": TEXT}}` with exit 0.
- Codex: the same `hookSpecificOutput` shapes for `SessionStart` and `PreToolUse` deny (exit 2
  with stderr and `{"decision": "block"}` are documented alternatives; Qew uses the first);
  stdin has `tool_name`, `tool_input`, `cwd`; matcher `Bash`.
- Cursor: `beforeShellExecution` stdin `{command, cwd, sandbox}`, output `{"permission":
  "allow"|"deny"|"ask", "user_message", "agent_message"}` (invalid output blocks, so allow is
  always explicit); `sessionStart` output `{"additional_context": TEXT}`;
  `afterShellExecution` cannot inject text.
- opencode: no deny protocol of its own; the plugin runs `qew hook pre-tool`, and exit code 2
  (reason on stdout) becomes a thrown error that blocks the tool call.

Decisions: Cursor also gets a `sessionStart` entry; refusals are not applied to `gh pr create`
(no requirement); a prior hook version's opencode plugin keeps its marker so install replaces it.

## VCS evidence hook

A commit cites ledger work with a trailer in the last paragraph of its message: `Refs: ID[, ID...]`
(`vcs_refs.py`; body prose, other casings, indented lines and tokens that are not ids are ignored).
`qew hook vcs-check` refuses (exit 2) a push when a cited id is unknown, a draft, or an open item with no
evidence for that commit, naming `qew evidence ID --change CHANGE`; completed and closed items are skipped (they
can no longer take evidence). Evidence matches the commit id or the jj change id by prefix.

- git: `qew hook install --git` writes the tracked, marker-owned `.githooks/pre-push` (which runs
  `qew hook vcs-check --pre-push`, and skips with a warning when qew is not on PATH) and sets the local
  `core.hooksPath`. It refuses a different existing `core.hooksPath` or an unowned script. The hook is bypassable
  only by deleting that tracked file (or unsetting the config), which shows in the diff. Remove deletes the script
  and unsets `core.hooksPath` when `.githooks` holds nothing else. A jj-only checkout has no git hooks (exit 2).
- jj: there is no pre-push hook, so `--revisions REVSET` serves scripts, and the agent pre-tool guard checks
  `jj git push` (the mutable ancestors of what `--bookmark`, `-r`, `--change`, `--all` or the working copy name) and
  denies with the same message. If jj cannot answer, the guard stays silent.
- `--message-file FILE` prints `known ID` or `unknown ID` per cited id and exits 2 when any is unknown.

# Superseded design (v0)

Everything below describes the old `qew hooks install` and warn-only `pre-shell` / `post-shell`
design. It is superseded by the section above and is removed with the old command set.

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

## Hook command shape (v1)

- Claude and Codex entries are one `command` string, no `args` (exec form is newer than some agent
  versions; on an older one a bare `qew` would exit 2 and block every shell call). The string is
  `command -v qew >/dev/null 2>&1 || { echo '{"systemMessage":"qew is not on PATH; qew hooks skipped"}'; exit 0; }; exec qew hook EVENT --agent NAME`
  (`hook_command.guarded`). A missing qew skips with a one-line warning (fail open); `exec` passes qew's own
  status through, so a real refusal (exit 2 / deny JSON) still blocks (fail closed).
- Install migrates the earlier exec-form and unguarded entries; remove deletes them too.
