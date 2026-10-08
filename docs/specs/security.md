# Trust

Item titles, bodies, comments, actor names and branch names are untrusted data: never evaluated, never used
as a path, command or agent instruction. Each guarantee below names the walks that prove it
(a bracketed tag with the guarantee id and one `walk:` entry per proving test); a test fails when a guarantee is missing or cites a walk that
does not exist.

## Reads

- Forge text (titles, bodies, labels, errors) and ledger text are untrusted data. Error detail is truncated
  before it is shown. A title with zero-width, bidirectional, tag or control characters is withheld in rows
  (`[title withheld: zero_width]`, JSON `title: null` plus `text_findings`).
  [guarantee: untrusted_data | walk: tests/test_untrusted_text_walk.py UntrustedTextWalk.test_zero_width_title_is_withheld_with_a_named_finding]
- Routine reads (`ready`, `list`, `blocked`, `search`, `prime`, hook context) print bounded metadata only:
  ids, states, priorities, labels and titles that are flattened, bounded and quoted when rewritten. They never
  print a body or a comment, and `ready` is capped (30 by default) with the cut reported.
  [guarantee: routine_reads_bounded | walk: tests/test_read_findings_walk.py BodiesWalk.test_routine_reads_never_print_a_body | walk: tests/test_read_list_walk.py ReadyLimitWalk.test_default_bound_reports_truncation_and_zero_returns_everything]
- Bodies and comments appear only in explicit inspection: `qew show`, `qew triage` (the only list-shaped read
  of unapproved text) and `qew list --bodies` (approved work only).
  [guarantee: explicit_inspection | walk: tests/test_read_findings_walk.py BodiesWalk.test_list_bodies_shows_bodies_of_approved_work_only | walk: tests/test_read_findings_walk.py TriageWalk.test_unapproved_instructions_are_data_here_and_absent_from_routine_channels]
- `qew prime` and the session-start hook re-validate approval from the ledger, quote and byte-bound every
  title, withhold titles with hidden-text findings (naming them under Contradictions), state that titles are
  untrusted data, and refuse to exceed their byte bound. An unreadable ledger is refused, never primed from.
  [guarantee: prime_revalidates | walk: tests/test_prime_walk.py SuspiciousTitleWalk.test_a_hidden_text_title_is_withheld_and_named_under_contradictions | walk: tests/test_prime_walk.py PrimeWalk.test_long_emoji_titles_are_cut_on_a_character_boundary_and_sections_are_capped | walk: tests/test_prime_walk.py PrimeWalk.test_an_unreadable_ledger_fails_prime_but_keeps_the_hook_silent]
- Text findings (`zero_width`, `bidi_control`, `unicode_tags`, `confusable`, `control`) are annotations from
  `src/qew/textscan.py`, whose vocabulary is pinned in the module (Unicode 16.0 ranges, a digest checked by a
  test) and never read from the host's Unicode database; text is never normalised or rewritten by a finding.
  [guarantee: findings_are_annotations | walk: tests/test_textscan_walk.py TextScanWalk.test_findings_do_not_depend_on_the_hosts_unicode_database | walk: tests/test_read_findings_walk.py FindingsWalk.test_triage_attributes_findings_to_title_and_body_and_spares_emoji]
- Identifiers, search terms and logins are data: never interpolated into a shell, a path or a forge query.
  `qew search` compares text; `qew inbox --login` accepts letters, digits, `-`, `_`, `.` only.
  [guarantee: terms_are_data | walk: tests/test_read_search_walk.py SearchWalk.test_terms_are_data_and_never_reach_a_shell | walk: tests/test_read_inbox_walk.py InboxArgumentWalk.test_a_login_cannot_carry_a_query_qualifier]
- Ledger files are validated before any read answers: unknown header keys, malformed events or event times,
  duplicate IDs and history for no item make every read exit 1 with nothing on stdout.

## Writes and transport

- Only a human's quoted words release work (`draft approve`, `create --quote`); `qew complete` is the only
  completion, and it needs evidence that is an ancestor of the target branch. Forge-side closes and merges
  do not count.
  [guarantee: approval_needs_a_quote | walk: tests/test_flow_walk.py FlowWalk.test_approve_without_a_human_quote_is_refused]
- Forge calls use bounded HTTP without redirects (a token cannot follow a redirect to another host); tokens
  come from the environment only and are never written to the ledger or config.
- Agent hooks refuse forge-side bypasses and never execute command text (see hooks.md).
- Python standard library only; no runtime dependencies.

## Token cost

`scripts/measure_reads.py` compares each compact `--json` answer with the ledger files a reader would
otherwise open, on a fixed fixture ledger, in exact UTF-8 bytes with a stdlib token proxy. The recorded sizes
are in `docs/specs/read-sizes.json`; `--check` fails when an answer grows past its recorded size
(`--record` rewrites them after a deliberate change). The current run:

```
command   raw bytes  compact bytes  tokens  saved
ready          7699            924     390    88%
list           7699           3068    1242    61%
prime          7699           1311     353    83%
show           4601           1380     513    71%
```
