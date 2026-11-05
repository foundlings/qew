# Scoped graph and progress views

The real graph joins parent ownership, prerequisites, tracking, PR and discovery links. A single-parent drawing would discard shared prerequisites and cross-cutting relationships. Keep canonical edges and node identities; terminal indentation is only a display traversal, with explicit references for repeated nodes. Parent trees remain the default progress view because they express genuine ownership. Dependency totals count each reachable work item once, never once per incoming path; ownership containers do not add work units. Intermediate prerequisites are work too: completing a bottom blocker must not mark its unfinished dependents 100%.

1. [x] Prove rooted selection and traversal failures with command walks.
2. [x] Add one flat traversal seam shared by graph and progress. Roots resolve ids/aliases; multiple roots are explicit. `--edges` selects children, parent, needs, blocks, tracks, tracked-by, pr, pr-for, discovered-from or discoveries. Graph defaults from a root to children plus needs; bare graph retains its legacy output. Progress defaults to children.
3. [x] Graph: depth bounds traversal; open-only retains paths to unfinished work; unmet-only drops satisfied needs edges. Tree, dot, Mermaid and structured JSON preserve shared links, isolated roots and missing targets. Canonical edge directions stay unchanged in drawings, even for reverse traversal.
4. [x] Progress: selected relationships define unique work-unit totals; depth and open-only bound display without changing totals. Repeated nodes become references. Valid mixed-direction cycles are references, not a reason to lose links; both views count unique reachable work units. Malformed parent/needs cycles refuse.
5. [x] Keep output escaping, old default contracts and one-snapshot behavior. Document commands, run focused and full gates, record evidence, merge through Qew, install and release.

Read-only motivating-ledger checks before release: `qew graph ADU --tree --depth 2 --open-only
--unmet-only` drew 87 rows at that snapshot; `qew progress ADU --edges children needs --tree
--open-only --depth 3` completed in about four seconds. These observations exercise the real ADU
relationships, not a copied or synthetic dependency tree. Live work can change these counts.
