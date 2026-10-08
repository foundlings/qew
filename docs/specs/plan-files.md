# Plan files

`qew apply` accepts one JSON object per line. It rejects the whole plan before
writing on unknown fields, invalid values, dangling local references, or cycles.

```jsonl
{"id":"plan","title":"Plan: Example","type":"epic","priority":"P1","campaign":{"title":"Example v1","due-on":"2027-03-31"},"project":"Example Factory","quote":"Sam: 'we need the example release tracked as a plan'"}
{"id":"first","title":"Build foundation","type":"task","parent":"plan","goal":"Provide the base.","done-when":["Golden walk passes."],"quote":"Sam: 'we need the example release tracked as a plan'","approved":true}
{"id":"second","title":"Use foundation","type":"task","parent":"plan","blocked-by":["first",42],"quote":"Sam: 'we need the example release tracked as a plan'"}
```

- `type`: `bug`, `enhancement`, `task`, or `epic`.
- `quote`: required on every line, `Name: 'their words'`, validated like
  `create --quote` before any write (including `--dry-run`). It is written into
  the body under `## Requested by`.
- `approved`: optional boolean, default false. Work packages carry
  `needs:approval` unless it is true; an epic with `approved` is refused.
- `priority`: `P0` through `P4`; default `P2`.
- `id`: optional and unique. Entries without one are keyed `line-N` (their
  line number); an explicit `id` equal to any other entry's key, generated or
  explicit, is refused.
- `parent` and `blocked-by`: local IDs or existing issue numbers.
- `areas`: list of existing area labels. Labels reserved by Qew (`qew:*`,
  `author:*`, priority, type, `epic`, `in-progress`) are refused with exit 2.
- Body: raw `body`, or structured `where`, `problem`/`goal`,
  `fix`/`approach`, and `done-when` fields.
- `discovered-from`: existing issue number recorded as provenance.
- An epic may declare `campaign` with `title` plus optional `description` and
  `due-on` (`YYYY-MM-DD`). The created plan issue is assigned to that milestone.
- An epic may declare a `project` title. Apply reconciles that organization
  Project after the graph and campaign exist. Repeated titles reconcile once.

`--dry-run` performs no writes. A real run checkpoints after every successful
creation, edge, campaign, or Project reconciliation. Resume requires the same
repository and exact plan digest and verifies Qew provenance on every issue
already recorded in the state file. Resume also verifies every checkpointed
parent, blocker, campaign assignment, Project identity, and created-work Project
membership against GitHub. Campaign and Project reconcilers are internally
idempotent, so interruption before their checkpoint is safe to retry.

Before each creation, apply searches the repository (all states) for the
entry's provenance marker. Exactly one issue with that marker and the entry's
title is adopted instead of creating a duplicate, recovering a creation lost
between GitHub and the checkpoint. More than one match is refused as ambiguous
(exit 2) before any creation; a malformed or truncated search fails loudly.
