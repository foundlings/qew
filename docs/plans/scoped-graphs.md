# Scoped graph and progress views

The real graph joins parent ownership, prerequisites, tracking, PR and discovery links. A single-parent drawing would discard shared prerequisites and cross-cutting relationships. Keep canonical edges and node identities; terminal indentation is only a display traversal, with explicit references for repeated nodes. Parent trees remain the default progress view because they express genuine ownership. Dependency totals count each reachable terminal item once, never once per incoming path.

1. [ ] Prove rooted selection and traversal failures with command walks.
2. [ ] Add one flat traversal seam shared by graph and progress. Roots resolve ids/aliases; multiple roots are explicit. `--edges` selects children, parent, needs, blocks, tracks, tracked-by, pr, pr-for, discovered-from or discoveries. Graph defaults from a root to children plus needs; bare graph retains its legacy output. Progress defaults to children.
3. [ ] Graph: depth bounds traversal; open-only retains paths to unfinished work; unmet-only drops satisfied needs edges. Tree, dot, Mermaid and structured JSON preserve shared links, isolated roots and missing targets. Canonical edge directions stay unchanged in drawings, even for reverse traversal.
4. [ ] Progress: selected relationships define unique terminal totals; depth and open-only bound display without changing totals. Repeated nodes become references. Cyclic progress traversals refuse rather than invent totals; graph can show mixed-direction cycles as references, but malformed parent/needs cycles refuse.
5. [ ] Keep output escaping, old default contracts and one-snapshot behavior. Document commands, run focused and full gates, record evidence, merge through Qew, install and release.
