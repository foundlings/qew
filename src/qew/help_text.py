"""One place for the --help description of every option and argument.

The parser declares the arguments; `describe` fills in the text of any that
declare none, so a flag never reaches `--help` bare. A test fails when one does.
"""

import argparse
from typing import Dict, Tuple

COMMON: Dict[str, str] = {
    "--repo": "repository as OWNER/REPO (default: this checkout's)",
    "--json": "print machine-readable JSON",
    "--dry-run": "show what would change and write nothing",
    "--body-file": "read the long-form body from FILE",
    "--edit": "write the body in $EDITOR",
    "--closed": "also act on a closed issue",
    "--reason": "why, recorded on the issue",
    "issue": "issue number",
    "issues": "issue numbers",
    "number": "pull request number",
    "pull_request": "pull request number",
}

ISSUE_TITLE = "issue title"
AGENT = "claude, codex, cursor, opencode, or all"
PROJECT_TITLE = "Project title (default: the project linked in .qew/config.json)"

SPECIFIC: Dict[Tuple[str, str], str] = {
    ("ready", "--limit"): "show at most N items; 0 shows all (default 30)",
    ("list", "--label"): "only work carrying this label; repeat it and every label must match",
    ("list", "--author"): "only work opened through Qew by LOGIN (the author:LOGIN label)",
    ("list", "--assignee"): "only work assigned to LOGIN",
    ("list", "--epic"): "only the children of plan N",
    ("list", "--state"): "open (default), closed or all",
    ("list", "--closed"): "same as --state closed",
    ("list", "--bodies"): "include each issue's body",
    ("inbox", "--login"): "whose inbox (default: the gh user)",
    ("inbox", "--since"): "replies since this UTC time or date (default: seven days ago)",
    ("inbox", "--count"): "print one summary line, for a session-start hook",
    ("triage", "--search"): "only issues whose title or body contains TERMS",
    ("search", "terms"): "search terms",
    ("init", "--mode"): "overlay (only qew:managed issues) or repository (every issue); always explicit",
    ("init", "--project"): "number of this checkout's Qew Project",
    ("campaign create", "--title"): "campaign (milestone) title",
    ("campaign create", "--description"): "campaign description",
    ("campaign create", "--due-on"): "due date, YYYY-MM-DD",
    ("campaign create", "--plan"): "attach this plan issue (repeatable)",
    ("campaign complete", "--title"): "campaign title",
    ("campaign status", "--title"): "campaign title (default: every campaign)",
    ("project ensure", "--title"): PROJECT_TITLE,
    ("project status", "--title"): PROJECT_TITLE,
    ("project prune", "--title"): PROJECT_TITLE,
    ("project prune", "--from"): "repository whose cards to remove from this board (OWNER/REPO)",
    ("hooks install", "agent"): AGENT,
    ("hooks remove", "agent"): AGENT,
    ("hook", "event"): "pre-shell or post-shell",
    ("hook", "--agent"): "the agent that calls the hook",
    ("adopt", "--dry-run"): "list the issues that would be marked managed",
    ("author backfill", "--dry-run"): "list the issues that would get an author label",
    ("area ensure", "name"): "team label name",
    ("start", "--priority"): "set the priority when the issue has none",
    ("complete", "--reason"): "what was done",
    ("close", "--reason"): "why the work is closed; recorded as the closing comment",
    ("close", "--completed"): "refused: names qew complete (claimed work) and --delivered-by (work others delivered)",
    ("close", "--abandoned"): "close as not planned (the work will not be done)",
    ("close", "--duplicate-of"): "close as a duplicate of this issue",
    ("close", "--delivered-by"): "close as completed: this commit already delivered the work (needs --landed-on)",
    ("close", "--landed-on"): "remote branch that must contain the --delivered-by commit",
    ("close", "--superseded-by"): "close as completed: this issue replaced the work",
    ("release", "--reason"): "why the claim is freed",
    ("reopen", "--reason"): "why the work is reopened",
    ("plan complete", "--reason"): "what completing the plan means",
    ("pr", "--for"): "issue the pull request is for (default: inferred from the branch)",
    ("pr", "--title"): "pull request title (default: derived from the issue)",
    ("pr", "--what"): "what the change does (default: the issue's fix or approach)",
    ("pr", "--why"): "why it is needed (default: the issue's problem or goal)",
    ("pr", "--testing"): "how it was tested",
    ("pr", "--body-file"): "use this body instead of --what, --why and --testing",
    ("pr", "--base"): "branch to merge into (default: the repository's default branch)",
    ("pr", "--ready"): "open it ready for review instead of as a draft",
    ("create", "--type"): "bug, enhancement or task",
    ("create", "--title"): ISSUE_TITLE,
    ("create", "--priority"): "P0 (critical) to P4 (backlog); default P2",
    ("create", "--area"): "team label to add (repeatable)",
    ("create", "--parent"): "parent plan issue",
    ("create", "--blocked-by"): "issue that must close first (repeatable)",
    ("create", "--discovered-from"): "issue whose work turned this up",
    ("create", "--where"): "bug: where it happens",
    ("create", "--problem"): "bug: what is wrong (excludes --goal)",
    ("create", "--goal"): "enhancement or task: what it should achieve (excludes --problem)",
    ("create", "--fix"): "bug: the intended fix (excludes --approach)",
    ("create", "--approach"): "enhancement or task: the intended approach (excludes --fix)",
    ("create", "--done-when"): "acceptance criterion (repeatable)",
    ("comment", "--body"): "the comment text",
    ("comment", "--body-file"): "read the comment from FILE",
    ("set", "--priority"): "replace the priority",
    ("set", "--type"): "replace the work type",
    ("set", "--add-area"): "add a team label (repeatable)",
    ("set", "--remove-area"): "remove a team label (repeatable)",
    ("set", "--parent"): "make this issue a child of ISSUE",
    ("set", "--no-parent"): "remove the parent",
    ("set", "--needs-approval"): "gate the work behind a human's go",
    ("set", "--no-needs-approval"): "release the gate",
    ("set", "--title"): "replace the title",
    ("set", "--body-file"): "replace the body with FILE",
    ("set", "--closed"): "allow editing a closed issue",
    ("block", "--on"): "the issue that blocks this one",
    ("unblock", "--from"): "the blocking issue to remove",
    ("apply", "plan"): "JSONL plan file",
    ("apply", "--state"): "checkpoint file (default: PLAN.state.json)",
    ("apply", "--throttle"): "pause between writes, such as 500ms (default), 2s or 0",
    ("plan create", "--title"): "plan title",
    ("plan create", "--priority"): "P0 (critical) to P4 (backlog); default P2",
    ("plan create", "--children"): "existing issues to attach, comma separated",
    ("plan create", "--goal"): "what the plan achieves",
    ("plan create", "--done-when"): "completion criterion (repeatable)",
    ("plan status", "issue"): "plan to show (default: every open plan)",
    ("draft list", "--parent"): "only the direct children of this issue",
}


def describe(parser: argparse.ArgumentParser, path: Tuple[str, ...] = ()) -> None:
    """Give every argument without a help line the text registered for it."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                describe(sub, (*path, name))
            continue
        if action.help or action.dest in ("help", "command"):
            continue
        key = action.option_strings[0] if action.option_strings else action.dest
        action.help = SPECIFIC.get((" ".join(path), key)) or COMMON.get(key)
