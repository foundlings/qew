"""Which module registers which top-level command, so `qew COMMAND` imports one command module and not all of them.

`tests/test_startup_walk.py` fails when this table and the modules' own `register` functions disagree.
"""

CORE_COMMANDS: frozenset[str] = frozenset({"check", "create", "draft", "init", "set"})  # registered by cli.py itself

COMMAND_MODULE: dict[str, str] = {
    "accept": "cmd_divergence",  "blocked": "cmd_read", "board": "cmd_board", "branch": "cmd_branch", "close": "cmd_finish",
    "comment": "cmd_comment", "correct-author": "cmd_author", "complete": "cmd_finish", "conflicts": "cmd_conflicts", "evidence": "cmd_finish",
    "export": "cmd_transfer", "forge": "cmd_forge", "graph": "cmd_read", "hook": "cmd_hook", "import": "cmd_forgeio",
    "inbox": "cmd_inbox", "item": "cmd_item_verbs", "land": "cmd_land", "ledger": "cmd_ledger", "list": "cmd_read", "migrate-ids": "cmd_migrate", "migrate-layout": "cmd_migrate", "next-id": "cmd_next_id",
    "mirror": "cmd_mirror", "plan": "cmd_plan", "pr": "cmd_pr", "prime": "cmd_prime", "pull": "cmd_pull", "push": "cmd_push", "project": "cmd_project",
    "ready": "cmd_read", "reject": "cmd_divergence", "release": "cmd_claim", "reopen": "cmd_finish", "repair-closes": "cmd_repair", "repair-evidence": "cmd_repair",
    "resolve": "cmd_conflicts", "restore": "cmd_forgeio", "search": "cmd_search", "show": "cmd_read", "start": "cmd_claim",
    "status": "cmd_status", "sync": "cmd_sync", "triage": "cmd_triage", "unblock": "cmd_block", "unlock": "cmd_unlock",
    "block": "cmd_block", "batch": "cmd_batch", "doctor": "cmd_doctor",
}
