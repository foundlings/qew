# Qew post-v1 roadmap

Compared with Hew [`v0.13.0`](https://github.com/lumberbarons/hew/releases/tag/v0.13.0)
and source commit [`c3ba620`](https://github.com/lumberbarons/hew/tree/c3ba620b7f5bbee97c5c9692f04604490ce4f48a)
(2026-09-25), especially its README and DESIGN contracts. The comparison is
behavioral: Qew does not copy features that weaken its model or duplicate a
trusted package manager.

## Already equivalent or stronger

- Native parent/dependency DAG, cycle refusal, ready work, explicit triage,
  guarded claims, PR linkage, hooks, and checkpointed JSONL application.
- Qew additionally requires explicit `qew:approved`, verifies completed
  checkpoint writes, projects campaigns and Projects, and ships provenance,
  SPDX, and hashes.
- Claim exits already distinguish another claimant (`3`) from the current
  caller (`5`); the existing command spec is stale.

## Delivered work

| Work | Delivered capability |
|---|---|
| CI and supply-chain maintenance | PR tests cover Python 3.10 and 3.12; dependencies and release actions are pinned and maintained. |
| Machine contract consistency | Hooks, lifecycle, and PR commands expose stable JSON and consistent global flags and exits. |
| Untrusted-text diagnostics | Triage and `show` attribute pinned Unicode findings to each displayed field. |
| Read and dedup UX | Derived ordering, capped-search guidance, triage handoff, and plan `next` are implemented. |
| Native plan authoring | `plan create`, verified initial children, and safe editor-backed bodies are implemented. |
| Claim-time triage | `start --priority` performs guarded, re-read priority triage without claim stealing. |
| Batch pacing | `apply --throttle` validates durations and defaults to 500ms between writes. |
| Code-shaped prose warnings | `create` and `pr` preserve bodies and advise on unmarked commands, flags, and paths. |
| Scoped auto-triage example | An inert least-authority Agentic Workflow example uses closed safe outputs. |
| Token measurements | A reproducible fixture harness reports bytes and a clearly labeled lexical-token proxy. |

## Deliberate non-goals

- No Beads migration.
- No claim-stealing `--force`; autonomous lanes must not evict one another.
- No `curl | sh` installer; signed release artifacts plus `pipx` remain the
  trusted-package-manager path.
- No memory store, offline database, or multi-repository primer without usage
  evidence.
- No OpenCode live-version matrix; command-level adapter coverage is sufficient
  per owner direction.

The executable work graph is [`qew-post-v1.jsonl`](qew-post-v1.jsonl).
