# Plan files

Decision: there is one path, `qew create --from-file FILE` (`-` for standard input). A separate `qew apply`
would repeat the same flags for no new capability, so it is not built.

A plan is JSON Lines (one object per line), one JSON object, or one JSON array of objects. Blank lines are
ignored. The whole plan is validated before the first write; a refusal is exit 2 and names the line (JSON Lines)
or entry (array).

```jsonl
{"ref": "plan", "kind": "epic", "title": "Ship the importer", "priority": 1}
{"ref": "parse", "kind": "issue", "title": "Parse the export", "parent": "@plan", "labels": ["area:import"], "quote": "Alexander: 'yes, build the importer first'"}
{"ref": "write", "kind": "issue", "title": "Write the items", "parent": "@plan", "needs": ["@parse"], "body": "Uses the parsed rows."}
{"kind": "task", "title": "Document the format", "parent": "@write"}
```

## Fields
`title` (required, one line), `kind` (`epic`, `issue` default, `task`), `parent`, `needs` (list), `labels`
(list), `priority` (non-negative integer, default 2), `body`, `ref`, `id`, `created`, `quote`, `approved`,
`events`. Any other field is refused. Names follow the item header keys.

- `ref` names an entry; `@ref` in `parent` or `needs` points at it, in any order. An entry without a `ref` is
  `line-N`. Keys are unique: a `ref` equal to another entry's key is refused. Other references are item ids or
  aliases already in the ledger.
- `id` keeps an explicit id (imports); an existing id is refused. `events` is accepted only with `--import`.
- `quote` releases the entry (filed approved with those words); without one the entry is a draft. `--quote` is
  the default for entries without their own. `approved: true` needs a quote; `approved: false` files a draft.
- Edges are checked against the whole graph: no self edge, unknown or duplicate need, `needs` cycle (new items
  and existing ones), or nesting other than epic > issue > task (a task may sit under an epic). Checks are
  iterative; a 3000-link chain is fine.
- The file must be a regular UTF-8 file, not a symbolic link, and name at least one entry.

## Flags
- `--draft`: file every entry as a draft; refuses `approved: true` lines and `--quote`.
- `--dry-run`: validate and print the operations without writing: `create` (with `draft`), `adopt`, `parent`,
  `need`. `--json` adds `schema_version` and `plan_sha256`.
- `--json`: `{"created": {key: id}, "adopted": [key]}`.

## Re-running
The first event of every created item carries `plan` (sha256 of the file bytes), `key` and `source` (file
name). The event is written before the item file, so a run killed in between leaves an orphan event. Running the
same file again adopts items by `(plan, key)`, writes the missing item files and creates the rest. Two items with
one marker are `ambiguous`; items of an earlier revision of the same file name are `plan revision mismatch`
(both exit 2, nothing written). Only the canonical event fields count as provenance; text in a body or title never
does. Plans read from standard input carry the source `-` and are not checked for revisions.
