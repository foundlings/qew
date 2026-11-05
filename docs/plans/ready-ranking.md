# Explain and select ready ranking

Measured behavior: ready already orders priority, longest open downstream chain, direct open dependents, id. Those scores are absent from its output; there is no transitive count or sort selector. A small global limit can hide useful P1 work behind P0 leaves.

- Keep default priority ordering compatible; `--by-priority` names it explicitly.
- `--by-dependents` orders unique transitive open dependents first, immediate ready unlocks second, then chain depth, direct dependents, priority and id. Expose every metric; do not invent an effort estimate or imply that every transitive waiter is immediately released.
- Show priority and query rank together in text and JSON. Count immediate unlocks only where this is the last unmet need and the dependent would actually be eligible for ready; gated, drafted or claimed items do not become new ready work.
- Optional roots limit eligible candidates to their ownership descendants. They do not cut dependency impact at the boundary: cross-cutting dependents remain real edges, and counts stay global. No artificial lane hierarchy is introduced.
- Measure unique reachability iteratively, not paths. Default bounded priority output computes expensive impact only for displayed rows. Both flags are mutually exclusive; eligibility and cycle refusals remain unchanged.
- Add command walks, make help/README/specs agree, and include this work with scoped graph views in the next release. Inspect the live ADU queue read-only before publishing.
