# Builder brief: qew v1 rewrite (from Santa, on the owner's behalf)

Read first: CLAUDE.md, docs/specs/ledger.md (THE contract), README.md, docs/specs/*.md.
The owner is not available for questions. Decide by the intent below; record decisions in docs/specs/.

## Intent
- qew = Python CLI that manages offline-first, file-based, mergeable LEDGER repositories
  (work items, dependencies, state, evidence) and syncs them to forges as mirrors.
- The ledger is the source of truth; forges are views. GitHub being down must never block work.
- Mechanical, deterministic parsing; token-efficient agent output; no prose parsing.
- Multi-forge: GitHub (incl. Projects v2), Forgejo/Gitea, GitLab. Any number of mirror targets,
  on multiple repositories across multiple forges; each item records every mirror (forge, repo url,
  issue number/id) in the ledger.
- qew stays project-independent (no foundlings-specific code).

## Work
1. Keep the existing package as the starting point; import commands file by file and rewrite what
   the ledger model needs. Remove GitHub-as-store assumptions; GitHub becomes one forge backend.
2. Implement the ledger: schema, header parser (stops at `---`), ULID-based IDs, append-only event
   files, derived reverse edges, `qew check` (schema, cycles, dangling needs, illegal transitions).
3. State machine + gated `qew complete` (all child tasks complete AND evidence change ids resolve as
   ancestors of the target branch of the code repo; jj and git supported).
4. Forge backend interface + GitHub, Forgejo/Gitea, GitLab backends; `qew sync` idempotent per target;
   `qew project ensure` boards with Draft / Ready / In progress / In review / Done.
5. Hooks for Claude AND Codex (keep cursor/opencode): SessionStart prime + PreToolUse refusals of
   forge-side bypasses (direct forge CLI edits of mirrored items, merges not through qew).
6. Keep the drafts model (draft -> approved only with a quoted human release).
7. Tests: command-level walks, RED commit before GREEN, offline (fake forge backends; no network in
   tests). Python stdlib first.
8. Tighten CLAUDE.md/README to the new model; keep them terse, bullet-style.

## VCS / delivery
- Repo: git@github.com:foundlings/qew.git (private). jj colocated; never squash; RED and GREEN separate.
- Bootstrap: commit to a branch `v1-ledger` and open a DRAFT PR on foundlings/qew; push often.
- Once the ledger + GitHub sync work, dogfood: create `foundlings/qew-ledger` content with qew,
  set `.qew/config.toml` in this repo, run `qew project ensure` for qew's own board, and track the
  remaining work through qew.
- Report progress to Santa by writing one line per milestone to
  /private/tmp/claude-502/-Users-887-workspace-foundlings/2c2c170a-03ee-40a8-9f38-5aee06075b4f/scratchpad/rudolph-inbox.txt
  prefixed `qew-builder:`.
- Do not mention any prior employer, product or origin anywhere.
- You may spawn subagents for parallel pieces (forge backends, hooks, tests).
