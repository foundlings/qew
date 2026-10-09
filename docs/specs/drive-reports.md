# Real test drives

Recorded results of the drives run against real GitHub with real git and jj clones (private repositories under the
`foundlings` organization). Every finding became a ledger issue with a scenario test.

## Replication drive (three checkouts of the live ledger)
- Converged: an item created on checkout A appeared on B and C on their next command; claim, evidence and completion on B
  appeared on A; an item created offline on C merged cleanly after it reconnected; ids stayed distinct.
- Human edits made directly on GitHub (rename, label, comment, reopen of a completed item) arrived as `field` and
  `commented` events with provenance `forge`, and the reopen became one `forge_divergence` (state) while ledger state was
  unchanged.
- Findings fixed: the board's `status:` labels were read back as human edits (171 divergences); mirror files rewritten by
  several machines produced real merge conflicts (identity is now committed once, sync state is local); two checkouts
  syncing one target created duplicate issues (lowest number wins, the later one closes itself); `qew status` flooded
  (capped); a stale clone wedged on old-format mirror conflicts (mirror-only conflicts resolve to the remote copy);
  fresh checkouts pushed stale titles over human edits (a missing baseline diverges instead of pushing); the ledger
  write lock queued writers behind slow pushes (push now runs after the lock); GitHub refused to change the close reason of
  a closed issue (reopen then close); the OS login leaked into events (actor comes from the git/jj name).

## Fork drive (authority, read-only mirror, migrate)
- `qew ledger fork` copied the live ledger (284 items) into a new private repository under a new identity
  (`ledger_id`, `forked_from`), with no mirrors and no claims. The original ledger, repository and board were untouched.
- The fork got its own board (org project 3) and its own primary GitHub repository; `project ensure` and `sync` created 16
  epic issues there.
- Claim race: two checkouts started the same epic at the same moment under the primary-forge rule; exactly one won
  ("claimed ... (1 items)") and the other was refused ("already claimed by 887 on the primary") without writing.
- A second repository added as a read-only mirror received the 17 epics one way.
- `qew forge migrate --to` verified 17 issues on the new target, made it primary and demoted the old primary to read-only.
- Finding: the repo-push hint (`set git_url and ledger_git_url`) printed on every sync; it is now quiet unless asked.
