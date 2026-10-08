# Trust

- Forge text (titles, bodies, labels, errors) is untrusted data: never evaluated, never used as a path,
  command or agent instruction. Error detail is truncated before it is shown.
- Titles in command output (`src/qew/untrusted_text.py`) are flattened to one line, cut at 96 bytes plus an
  ellipsis, and quoted once rewritten; a title with zero-width, bidirectional or control characters is
  withheld (`[title withheld: zero_width]`, JSON `title: null` plus `text_findings`). `qew check` reports
  `text_findings` for approved or claimed items. A runnable command is never printed beside a rewritten title.
- Ledger files are validated on read: unknown header keys, malformed events, duplicate IDs, cycles and
  dangling edges fail loudly. Truncated or partial files are never guessed around.
- Forge calls use bounded HTTP without redirects (a token cannot follow a redirect to another host);
  tokens come from the environment only and are never written to the ledger or config.
- Only a human's quoted words release work (`draft approve`); `qew complete` is the only completion, and
  it needs evidence that is an ancestor of the target branch. Forge-side closes and merges do not count.
- Agent hooks refuse forge-side bypasses and never execute command text (see hooks.md).
- Python standard library only; no runtime dependencies.
