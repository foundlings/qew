# Explain and select ready ranking

Measured behavior: ready already orders priority, longest open downstream chain, direct open dependents, id. Those scores are absent from its output; there is no transitive count or sort selector. A small global limit can hide useful P1 work behind P0 leaves.

- Keep default priority ordering compatible; `--by-priority` names it explicitly.
- `--by-dependents` orders unique transitive open dependents first, immediate ready unlocks second, then chain depth, direct dependents, priority and id. Expose every metric; do not invent an effort estimate or imply that every transitive waiter is immediately released.
- Show priority and query rank together in text and JSON. Count immediate unlocks only where this is the last unmet need and the dependent would actually be eligible for ready; gated, drafted or claimed items do not become new ready work.
- Optional roots limit eligible candidates to their ownership descendants. They do not cut dependency impact at the boundary: cross-cutting dependents remain real edges, and counts stay global. No artificial lane hierarchy is introduced.
- Measure unique reachability iteratively, not paths. Default bounded priority output computes expensive impact only for displayed rows. Both flags are mutually exclusive; eligibility and cycle refusals remain unchanged.
- Add command walks, make help/README/specs agree, and include this work with scoped graph views in the next release. Inspect the live ADU queue read-only before publishing.

## Read-only check of the motivating ledger

Before release, the local foundlings ledger query `qew ready 0436j477k --by-dependents --limit 12 --json`
returned 26 ready candidates in Phase 3. At that check, 043h35dev (group 14b) ranked first with
6 unique downstream items, 1 direct dependent and 1 immediate unlock. 043h31he9 and 043h328bo
(groups 42c and 42d) each had 4 downstream items, 1 direct dependent and 0 immediate unlocks.
The priority query over the same scope put those P0 roots ahead of the P1 group-32 item; the impact
query put group 32 second because it had 4 direct waiters. These are snapshot observations, not fixed
rankings: work and priorities can change while agents run. Counts explain potential versus immediate
release; they do not replace lane assignment or establish that a multi-input gate can be released by
one prerequisite alone.
