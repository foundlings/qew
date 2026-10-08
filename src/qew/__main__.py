"""Qew command-line entry point."""

import argparse
import json
import shlex
import sys
from typing import Optional, Sequence

from qew import __version__
from qew.apply_plan import execute as apply_plan, parse_throttle
from qew.bootstrap import initialize
from qew.campaign import campaign_statuses, render_campaign_title, complete_campaign, create_campaign
from qew.code_prose import unmarked_code_tokens
from qew.adoption import adopt
from qew.authors import backfill as backfill_authors
from qew.areas import DEFAULT_COLOR, DEFAULT_DESCRIPTION, ensure_area
from qew.areas import validate as validate_area
from qew.config import RepositoryMode, linked_project, load_config, repository_root, use_repository
from qew.claude_hook import install as install_claude_hook
from qew.claude_hook import remove as remove_claude_hook
from qew.codex_hook import install as install_codex_hook
from qew.codex_hook import remove as remove_codex_hook
from qew.cursor_hook import install as install_cursor_hook
from qew.cursor_hook import remove as remove_cursor_hook
from qew.diagnostics import graph_report
from qew.edit import SetRequest, set_issue
from qew.editor import edit_body
from qew.epic_claims import take_changed as take_changed_epics
from qew.errors import QewError
from qew.graph import block, unblock
from qew.github import (
    fetch_blocked_issues,
    fetch_draft_issues,
    fetch_dependency_graph,
    fetch_plan_status_graph,
    fetch_prime_graph,
    fetch_ready_graph,
    fetch_show_issue,
    fetch_triage_issues,
    fetch_vetted_issues,
    resolve_repository,
    search_vetted_issues,
)
from qew.lifecycle import close as close_work
from qew.land import land
from qew.land import render as render_land
from qew.lifecycle import (
    complete,
    completion_evidence,
    issue_claim,
    release,
    reopen,
    start,
)
from qew.opencode_hook import install as install_opencode_hook
from qew.opencode_hook import remove as remove_opencode_hook
from qew.plan_status import derive_plan_statuses
from qew.plan_authoring import PlanCreateRequest, create_plan
from qew.plan_completion import complete_plan
from qew.prime import prime_json, render_prime
from qew.project import ensure_project, project_status, prune_project, sync_cards
from qew.pull_request import PullRequest, create as create_pull_request
from qew.pull_request import mark_ready as mark_pull_request_ready_for_review
from qew import checklist
from qew.comment import add_comment
from qew.drafts import approve as approve_drafts
from qew.drafts import revert as revert_to_draft
from qew.inbox import build as build_inbox
from qew.inbox import count_line as inbox_count_line
from qew.inbox import rows as inbox_rows
from qew.inbox import since_or_default
from qew.inbox import text_lines as inbox_text_lines
from qew.merge import merge as merge_pull_request_command
from qew.read_suite import blocked_items, draft_items, list_items, search_is_capped, search_items, show_item, triage_items
from qew.ready import derive_ready, ready_json
from qew.help_text import describe as describe_arguments
from qew.hook_run import message as hook_message
from qew.hook_run import render as hook_render
from qew.requester import require_quote
from qew.work import CreateRequest, create


EPILOG = """Commands by workflow:

  Read
    qew ready          list actionable work, highest priority first
    qew list           list vetted work (--epic, --label, --author, --assignee, --state)
    qew inbox          what waits on a person: replies, approvals owed, assigned work
    qew show ISSUE     inspect one issue, drafts included
    qew search TERMS   search vetted work
    qew triage         inspect issues missing a type or priority
    qew blocked        explain waiting work

  Work
    qew create         file approved work (--needs-approval to gate it)
    qew set ISSUE      edit title, body, type, priority, areas, parent
    qew comment ISSUE  add a comment
    qew block ISSUE --on N     add a blocking dependency
    qew unblock ISSUE --from N remove one
    qew start ISSUE    claim ready work
    qew complete ISSUE finish claimed work with proof
    qew close ISSUE    close unclaimed work: abandoned, duplicate, delivered or superseded
    qew release ISSUE  free a claim
    qew reopen ISSUE   reopen work
    qew land PR        complete the issues a merged pull request closed

  Drafts (filed and discussed, never worked until approved)
    qew create --draft         file a draft issue
    qew plan create --draft    file a draft plan (epic)
    qew draft list             list open drafts
    qew draft approve ISSUE... [--tree] --quote Q   release drafts into ready work
    qew draft revert ISSUE...  [--tree] --reason R --quote Q   return work to draft

  Pull requests
    qew pr --for ISSUE         open a draft pull request for claimed work
    qew pr check PR ITEM...    tick checklist items (or --all, --list)
    qew pr ready PR            mark a draft ready for review
    qew pr merge PR            merge a ready pull request and land its issues

  Plans and graph
    qew plan create|status|complete   parent plans and their rollups
    qew campaign       milestone campaigns
    qew apply FILE     apply a JSONL work plan (--draft files it as drafts)
    qew graph check    check the dependency graph

  Board and repository
    qew init           reconcile labels and repository mode
    qew project        GitHub Projects board (ensure, status, prune)
    qew adopt          mark Qew-created issues managed
    qew author         author labels
    qew area           team (area) labels

  Hooks and agent context
    qew hooks          install agent shell-command hooks
    qew hook           run by an agent around shell commands
    qew prime          bounded session context for an agent

Run qew COMMAND --help for a command's options."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qew", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", dest="global_repo", metavar="OWNER/REPO")
    parser.add_argument("--json", action="store_true", dest="global_as_json")
    parser.add_argument("--version", action="version", version=f"qew {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    ready = commands.add_parser("ready", help="list actionable work")
    ready.add_argument("--repo", metavar="OWNER/REPO")
    ready.add_argument("--json", action="store_true", dest="as_json")
    ready.add_argument("--limit", type=int, default=30)
    prime = commands.add_parser("prime", help="emit bounded agent session context")
    prime.add_argument("--repo", metavar="OWNER/REPO")
    prime.add_argument("--json", action="store_true", dest="as_json")
    list_command = commands.add_parser("list", help="list vetted work")
    list_command.add_argument("--repo", metavar="OWNER/REPO")
    list_command.add_argument("--label", action="append")
    list_command.add_argument("--author", metavar="LOGIN")
    list_command.add_argument("--assignee", metavar="LOGIN")
    list_command.add_argument("--epic", type=int)
    list_command.add_argument("--state", choices=("open", "closed", "all"), default="open")
    list_command.add_argument("--closed", action="store_const", const="closed", dest="state")
    list_command.add_argument("--bodies", action="store_true")
    list_command.add_argument("--json", action="store_true", dest="as_json")
    inbox = commands.add_parser("inbox", help="show replies, waiting approvals and assigned work")
    inbox.add_argument("--repo", metavar="OWNER/REPO")
    inbox.add_argument("--login", metavar="LOGIN")
    inbox.add_argument("--since", metavar="TIME")
    inbox.add_argument("--count", action="store_true")
    inbox.add_argument("--json", action="store_true", dest="as_json")
    show = commands.add_parser("show", help="inspect one issue")
    show.add_argument("issue", type=int)
    show.add_argument("--repo", metavar="OWNER/REPO")
    show.add_argument("--json", action="store_true", dest="as_json")
    search = commands.add_parser("search", help="search vetted work")
    search.add_argument("terms", nargs="+")
    search.add_argument("--repo", metavar="OWNER/REPO")
    search.add_argument("--json", action="store_true", dest="as_json")
    triage = commands.add_parser("triage", help="inspect untriaged issues")
    triage.add_argument("--search")
    triage.add_argument("--repo", metavar="OWNER/REPO")
    triage.add_argument("--json", action="store_true", dest="as_json")
    blocked = commands.add_parser("blocked", help="explain waiting work")
    blocked.add_argument("--repo", metavar="OWNER/REPO")
    blocked.add_argument("--json", action="store_true", dest="as_json")
    init = commands.add_parser("init", help="reconcile Qew repository labels")
    init.add_argument("--repo", metavar="OWNER/REPO")
    init.add_argument("--mode", choices=("overlay", "repository"))
    init.add_argument("--project", type=int, metavar="NUMBER")
    init.add_argument("--json", action="store_true", dest="as_json")
    campaign = commands.add_parser("campaign", help="manage milestone campaigns")
    campaign_commands = campaign.add_subparsers(dest="campaign_command", required=True)
    campaign_create = campaign_commands.add_parser("create", help="create or reconcile a campaign")
    campaign_create.add_argument("--repo", metavar="OWNER/REPO")
    campaign_create.add_argument("--title", required=True)
    campaign_create.add_argument("--description")
    campaign_create.add_argument("--due-on")
    campaign_create.add_argument("--plan", type=int, action="append", default=[])
    campaign_create.add_argument("--quote", help="the human requester: Name: 'their words'")
    campaign_create.add_argument("--json", action="store_true", dest="as_json")
    campaign_complete = campaign_commands.add_parser("complete", help="close a completed campaign")
    campaign_complete.add_argument("--repo", metavar="OWNER/REPO")
    campaign_complete.add_argument("--title", required=True)
    campaign_complete.add_argument("--json", action="store_true", dest="as_json")
    campaign_status = campaign_commands.add_parser("status", help="show campaign completion readiness")
    campaign_status.add_argument("--repo", metavar="OWNER/REPO")
    campaign_status.add_argument("--title")
    campaign_status.add_argument("--json", action="store_true", dest="as_json")
    project = commands.add_parser("project", help="manage GitHub Projects visibility")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_ensure = project_commands.add_parser("ensure", help="create or reconcile a project")
    project_ensure.add_argument("--repo", metavar="OWNER/REPO")
    project_ensure.add_argument("--title")
    project_ensure.add_argument("--json", action="store_true", dest="as_json")
    project_prune = project_commands.add_parser(
        "prune", help="delete one repository's cards from the board"
    )
    project_prune.add_argument("--from", required=True, dest="source", metavar="OWNER/REPO")
    project_prune.add_argument("--repo", metavar="OWNER/REPO")
    project_prune.add_argument("--title")
    project_prune.add_argument("--json", action="store_true", dest="as_json")
    project_status_command = project_commands.add_parser(
        "status", help="report Project drift without writing"
    )
    project_status_command.add_argument("--repo", metavar="OWNER/REPO")
    project_status_command.add_argument("--title")
    project_status_command.add_argument("--json", action="store_true", dest="as_json")
    hooks = commands.add_parser("hooks", help="manage agent shell-command hooks")
    hook_commands = hooks.add_subparsers(dest="hook_command", required=True)
    hook_install = hook_commands.add_parser("install", help="install agent shell-command hooks")
    hook_install.add_argument("agent", choices=("claude", "codex", "cursor", "opencode", "all"))
    hook_install.add_argument("--json", action="store_true", dest="as_json")
    hook_remove = hook_commands.add_parser("remove", help="remove agent shell-command hooks")
    hook_remove.add_argument("agent", choices=("claude", "codex", "cursor", "opencode", "all"))
    hook_remove.add_argument("--json", action="store_true", dest="as_json")
    hook_run_command = commands.add_parser(
        "hook", help="run by an agent around shell commands (reads its payload on stdin)"
    )
    hook_run_command.add_argument("event", choices=("pre-shell", "post-shell"))
    hook_run_command.add_argument(
        "--agent", required=True, choices=("claude", "codex", "cursor", "opencode")
    )
    adopt_command = commands.add_parser(
        "adopt", help="mark Qew-created issues that lack qew:managed as managed"
    )
    adopt_command.add_argument("--repo", metavar="OWNER/REPO")
    adopt_command.add_argument("--author", metavar="LOGIN", help="only issues opened by LOGIN")
    adopt_command.add_argument("--dry-run", action="store_true")
    adopt_command.add_argument("--json", action="store_true", dest="as_json")
    author = commands.add_parser("author", help="manage author labels")
    author_commands = author.add_subparsers(dest="author_command", required=True)
    author_backfill = author_commands.add_parser(
        "backfill", help="label existing issues without an author label with their opener"
    )
    author_backfill.add_argument("--repo", metavar="OWNER/REPO")
    author_backfill.add_argument("--dry-run", action="store_true")
    author_backfill.add_argument("--json", action="store_true", dest="as_json")
    area = commands.add_parser("area", help="manage team (area) labels")
    area_commands = area.add_subparsers(dest="area_command", required=True)
    area_ensure = area_commands.add_parser(
        "ensure", help="create a missing area label; never overwrites an existing one"
    )
    area_ensure.add_argument("name")
    area_ensure.add_argument("--repo", metavar="OWNER/REPO")
    area_ensure.add_argument("--color", metavar="HEX", help="six hex digits (default EDEDED)")
    area_ensure.add_argument("--description", help="one line (default: Team label)")
    area_ensure.add_argument("--json", action="store_true", dest="as_json")
    start = commands.add_parser("start", help="claim ready work")
    start.add_argument("issue", type=int)
    start.add_argument("--repo", metavar="OWNER/REPO")
    start.add_argument("--priority", choices=("P0", "P1", "P2", "P3", "P4"))
    start.add_argument("--lane", help="name the agent lane holding the claim")
    start.add_argument("--workspace", help="absolute path where the lane works (needs --lane)")
    start.add_argument(
        "--kind",
        choices=("compute", "token"),
        help="what the lane does: compute (builds and tests, the default) or token (text only); needs --lane",
    )
    start.add_argument(
        "--take-over",
        action="store_true",
        help="deliberately take work another login holds; needs --reason",
    )
    start.add_argument("--reason", help="why the claim is taken over (with --take-over)")
    start.add_argument("--json", action="store_true", dest="as_json")
    complete = commands.add_parser("complete", help="complete claimed work")
    complete.add_argument("issue", type=int)
    complete.add_argument("--repo", metavar="OWNER/REPO")
    complete.add_argument("--reason", required=True)
    complete.add_argument("--commit", metavar="SHA", help="commit that did the work")
    complete.add_argument(
        "--landed-on", metavar="BRANCH", help="remote branch the commit must already be on"
    )
    complete.add_argument(
        "--no-commit", metavar="REASON", help="record why this work has no commit"
    )
    complete.add_argument(
        "--take-over",
        action="store_true",
        help="deliberately finish work another login holds, clearing their claim",
    )
    complete.add_argument("--json", action="store_true", dest="as_json")
    close = commands.add_parser("close", help="close work with an explicit disposition")
    close.add_argument("issue", type=int)
    close.add_argument("--repo", metavar="OWNER/REPO")
    close.add_argument("--reason", required=True)
    disposition = close.add_mutually_exclusive_group()
    disposition.add_argument("--completed", action="store_true")
    disposition.add_argument("--abandoned", action="store_true")
    disposition.add_argument("--duplicate-of", type=int)
    disposition.add_argument("--delivered-by", metavar="SHA")
    disposition.add_argument("--superseded-by", type=int, metavar="ISSUE")
    close.add_argument("--landed-on", metavar="BRANCH")
    close.add_argument("--json", action="store_true", dest="as_json")
    land_command = commands.add_parser(
        "land", help="complete the claimed issues a merged pull request closed"
    )
    land_command.add_argument("pull_request", type=int, metavar="PR")
    land_command.add_argument("--repo", metavar="OWNER/REPO")
    land_command.add_argument("--json", action="store_true", dest="as_json")
    release_command = commands.add_parser("release", help="free a claim without closing the work")
    release_command.add_argument("issue", type=int)
    release_command.add_argument("--repo", metavar="OWNER/REPO")
    release_command.add_argument("--reason", required=True)
    release_command.add_argument("--lane", help="the caller's lane; must match a lane claim unless --force")
    release_command.add_argument(
        "--force", action="store_true", help="release a claim held by another login or lane (recorded)"
    )
    release_command.add_argument("--json", action="store_true", dest="as_json")
    reopen_command = commands.add_parser("reopen", help="reopen work and release stale claims")
    reopen_command.add_argument("issue", type=int)
    reopen_command.add_argument("--repo", metavar="OWNER/REPO")
    reopen_command.add_argument("--reason", required=True)
    reopen_command.add_argument("--json", action="store_true", dest="as_json")
    pull_request = commands.add_parser("pr", help="pull requests: open, check, ready, merge")
    pull_request.add_argument("--for", type=int, dest="issue")
    pull_request.add_argument("--repo", metavar="OWNER/REPO")
    pull_request.add_argument("--title")
    pull_request.add_argument("--what")
    pull_request.add_argument("--why")
    pull_request.add_argument("--testing")
    pull_request.add_argument("--body-file")
    pull_request.add_argument("--base")
    pull_request.add_argument("--head", help="pushed branch; skips branch derivation")
    pull_request.add_argument("--ready", action="store_true")
    pull_request.add_argument("--json", action="store_true", dest="as_json")
    pull_commands = pull_request.add_subparsers(dest="pr_command")
    pull_ready = pull_commands.add_parser("ready", help="mark your draft pull request ready")
    pull_ready.add_argument("number", type=int, metavar="PR")
    pull_ready.add_argument("--repo", metavar="OWNER/REPO", default=argparse.SUPPRESS)
    pull_ready.add_argument(
        "--json", action="store_true", dest="as_json", default=argparse.SUPPRESS
    )
    pull_check = pull_commands.add_parser(
        "check", help="tick items of your pull request checklist"
    )
    pull_check.add_argument("number", type=int, metavar="PR")
    pull_check.add_argument("items", nargs="*", metavar="ITEM", help="marker id or position")
    pull_check.add_argument("--all", action="store_true", dest="all_items", help="tick every open item")
    pull_check.add_argument("--list", action="store_true", dest="list_items", help="show the items")
    pull_check.add_argument("--repo", metavar="OWNER/REPO", default=argparse.SUPPRESS)
    pull_check.add_argument(
        "--json", action="store_true", dest="as_json", default=argparse.SUPPRESS
    )
    pull_merge = pull_commands.add_parser(
        "merge", help="merge your ready pull request and land its issues"
    )
    pull_merge.add_argument("number", type=int, metavar="PR")
    pull_merge.add_argument(
        "--auto", action="store_true", help="enable auto-merge (needs required checks on the base)"
    )
    pull_merge.add_argument("--admin", action="store_true", help="bypass branch protection; needs --reason")
    pull_merge.add_argument(
        "--other-author",
        action="store_true",
        help="merge another login's pull request without holding its claims; needs --reason",
    )
    pull_merge.add_argument("--reason", help="why an admin or other-author merge is justified")
    pull_merge.add_argument("--repo", metavar="OWNER/REPO", default=argparse.SUPPRESS)
    pull_merge.add_argument(
        "--json", action="store_true", dest="as_json", default=argparse.SUPPRESS
    )
    create = commands.add_parser("create", help="create approved work")
    create.add_argument("--repo", metavar="OWNER/REPO")
    create.add_argument("--type", required=True, choices=("bug", "enhancement", "task"))
    create.add_argument("--title", required=True)
    create.add_argument("--priority", choices=("P0", "P1", "P2", "P3", "P4"), default="P2")
    create.add_argument("--area", action="append", default=[])
    create.add_argument("--parent", type=int)
    create.add_argument("--blocked-by", type=int, action="append", default=[])
    create.add_argument("--discovered-from", type=int)
    create.add_argument(
        "--needs-approval",
        action="store_true",
        help="file work a human must authorize before it starts; never ready",
    )
    create.add_argument("--where")
    create.add_argument("--problem")
    create.add_argument("--goal")
    create.add_argument("--fix")
    create.add_argument("--approach")
    create.add_argument("--done-when", action="append", default=[])
    create.add_argument("--body-file")
    create.add_argument("--edit", action="store_true")
    create.add_argument("--quote", help="the human requester: Name: 'their words'")
    create.add_argument(
        "--approved",
        action="store_true",
        help="the quote also authorizes starting the work; omit needs:approval",
    )
    create.add_argument(
        "--draft",
        action="store_true",
        help="file a draft: discussed and edited, never worked until qew draft approve",
    )
    create.add_argument("--json", action="store_true", dest="as_json")
    comment = commands.add_parser("comment", help="add a comment to an issue")
    comment.add_argument("issue", type=int)
    comment.add_argument("--repo", metavar="OWNER/REPO")
    comment.add_argument("--body")
    comment.add_argument("--body-file")
    comment.add_argument("--json", action="store_true", dest="as_json")
    draft = commands.add_parser("draft", help="list, approve and revert draft work")
    draft_commands = draft.add_subparsers(dest="draft_command", required=True)
    draft_list = draft_commands.add_parser("list", help="list open drafts")
    draft_list.add_argument("--parent", type=int, help="only direct children of this issue")
    draft_list.add_argument("--repo", metavar="OWNER/REPO", default=argparse.SUPPRESS)
    draft_list.add_argument(
        "--json", action="store_true", dest="as_json", default=argparse.SUPPRESS
    )
    draft_approve = draft_commands.add_parser(
        "approve", help="release drafts: remove qew:draft, add qew:approved"
    )
    draft_approve.add_argument("issues", type=int, nargs="+", metavar="ISSUE")
    draft_approve.add_argument("--tree", action="store_true", help="also approve open draft descendants")
    draft_approve.add_argument("--quote", help="the human who approves: Name: 'their words'")
    draft_approve.add_argument("--repo", metavar="OWNER/REPO", default=argparse.SUPPRESS)
    draft_approve.add_argument(
        "--json", action="store_true", dest="as_json", default=argparse.SUPPRESS
    )
    draft_revert = draft_commands.add_parser(
        "revert", help="return approved work to draft (unclaimed, no open pull request)"
    )
    draft_revert.add_argument("issues", type=int, nargs="+", metavar="ISSUE")
    draft_revert.add_argument("--tree", action="store_true", help="also revert open descendants")
    draft_revert.add_argument("--reason", help="why the work goes back to draft")
    draft_revert.add_argument("--quote", help="the human who decides: Name: 'their words'")
    draft_revert.add_argument("--repo", metavar="OWNER/REPO", default=argparse.SUPPRESS)
    draft_revert.add_argument(
        "--json", action="store_true", dest="as_json", default=argparse.SUPPRESS
    )
    set_command = commands.add_parser("set", help="edit canonical work fields")
    set_command.add_argument("issue", type=int)
    set_command.add_argument("--repo", metavar="OWNER/REPO")
    set_command.add_argument("--priority", choices=("P0", "P1", "P2", "P3", "P4"))
    set_command.add_argument("--type", choices=("bug", "enhancement", "task"), dest="work_type")
    set_command.add_argument("--add-area", action="append", default=[])
    set_command.add_argument("--remove-area", action="append", default=[])
    parent = set_command.add_mutually_exclusive_group()
    parent.add_argument("--parent", type=int)
    parent.add_argument("--no-parent", action="store_true")
    approval = set_command.add_mutually_exclusive_group()
    approval.add_argument("--needs-approval", action="store_true", default=None)
    approval.add_argument("--no-needs-approval", action="store_false", dest="needs_approval")
    set_command.add_argument("--title")
    set_command.add_argument("--body-file")
    set_command.add_argument("--closed", action="store_true")
    set_command.add_argument("--json", action="store_true", dest="as_json")
    block = commands.add_parser("block", help="add a blocking dependency")
    block.add_argument("issue", type=int)
    block.add_argument("--on", required=True, type=int, dest="blocker")
    block.add_argument("--repo", metavar="OWNER/REPO")
    block.add_argument("--json", action="store_true", dest="as_json")
    block.add_argument("--closed", action="store_true")
    unblock = commands.add_parser("unblock", help="remove a blocking dependency")
    unblock.add_argument("issue", type=int)
    unblock.add_argument("--from", required=True, type=int, dest="blocker")
    unblock.add_argument("--repo", metavar="OWNER/REPO")
    unblock.add_argument("--json", action="store_true", dest="as_json")
    unblock.add_argument("--closed", action="store_true")
    apply_plan = commands.add_parser("apply", help="apply a JSONL work plan")
    apply_plan.add_argument("plan")
    apply_plan.add_argument("--repo", metavar="OWNER/REPO")
    apply_plan.add_argument("--state")
    apply_plan.add_argument("--dry-run", action="store_true")
    apply_plan.add_argument(
        "--draft",
        action="store_true",
        help="create every item as a draft; approve them later with qew draft approve --tree",
    )
    apply_plan.add_argument("--throttle", default="500ms")
    apply_plan.add_argument("--json", action="store_true", dest="as_json")
    graph = commands.add_parser("graph", help="inspect graph invariants")
    graph_commands = graph.add_subparsers(dest="graph_command", required=True)
    graph_check = graph_commands.add_parser("check", help="check cycles and contradictions")
    graph_check.add_argument("--repo", metavar="OWNER/REPO")
    graph_check.add_argument("--json", action="store_true", dest="as_json")
    plan = commands.add_parser("plan", help="create and inspect plans")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    plan_create = plan_commands.add_parser("create", help="create an approved parent plan")
    plan_create.add_argument("--repo", metavar="OWNER/REPO")
    plan_create.add_argument("--title", required=True)
    plan_create.add_argument("--priority", choices=("P0", "P1", "P2", "P3", "P4"), default="P2")
    plan_create.add_argument("--children")
    plan_create.add_argument("--goal")
    plan_create.add_argument("--done-when", action="append", default=[])
    plan_create.add_argument("--body-file")
    plan_create.add_argument("--edit", action="store_true")
    plan_create.add_argument("--quote", help="the human requester: Name: 'their words'")
    plan_create.add_argument(
        "--draft", action="store_true", help="file a draft plan; approve it with qew draft approve"
    )
    plan_create.add_argument("--json", action="store_true", dest="as_json")
    plan_status = plan_commands.add_parser("status", help="roll up plan children")
    plan_status.add_argument("issue", type=int, nargs="?")
    plan_status.add_argument("--repo", metavar="OWNER/REPO")
    plan_status.add_argument("--json", action="store_true", dest="as_json")
    plan_complete = plan_commands.add_parser("complete", help="close a completed parent plan")
    plan_complete.add_argument("issue", type=int)
    plan_complete.add_argument("--reason", required=True)
    plan_complete.add_argument("--repo", metavar="OWNER/REPO")
    plan_complete.add_argument("--json", action="store_true", dest="as_json")
    describe_arguments(parser)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        local_repository = getattr(args, "repo", None)
        if args.global_repo is not None and local_repository is not None:
            if args.global_repo != local_repository:
                raise QewError("conflicting --repo values", 2)
        args.repo = local_repository or args.global_repo
        args.as_json = getattr(args, "as_json", False) or args.global_as_json
        if args.command == "hook":
            return _run_hook(args.event, args.agent)
        if args.command == "hooks":
            if args.repo is not None:
                raise QewError("hooks are local and do not accept --repo", 2)
            root = repository_root()
            adapters = {
                "claude": (install_claude_hook, remove_claude_hook),
                "codex": (install_codex_hook, remove_codex_hook),
                "cursor": (install_cursor_hook, remove_cursor_hook),
                "opencode": (install_opencode_hook, remove_opencode_hook),
            }
            agents = list(adapters) if args.agent == "all" else [args.agent]
            results = []
            for agent in agents:
                install, remove = adapters[agent]
                if args.hook_command == "install":
                    changed = install(root)
                    action = "installed" if changed else "unchanged"
                else:
                    changed = remove(root)
                    action = "removed" if changed else "unchanged"
                results.append(
                    {
                        "action": action,
                        "agent": agent,
                        "schema_version": 1,
                        "trust_required": agent == "codex" and args.hook_command == "install",
                    }
                )
            if args.agent == "all" and args.as_json:
                _render_result({"agents": results, "schema_version": 1}, "", True)
                return 0
            for result in results:
                _render_result(result, f"{result['action']} {result['agent']} hook", args.as_json)
            if any(result["trust_required"] for result in results) and not args.as_json:
                print("review and trust it with /hooks before it will run")
            return 0
        if args.command == "adopt":
            repository = resolve_repository(args.repo)
            use_repository(repository)
            report = adopt(repository, args.author, args.dry_run)
            count = len(report["adopted"])
            human = (
                "nothing to adopt"
                if not count
                else f"{'would adopt' if args.dry_run else 'adopted'} {count} issues"
            )
            _render_result(report, human, args.as_json)
            return 0
        if args.command == "author":
            repository = resolve_repository(args.repo)
            use_repository(repository)
            report = backfill_authors(repository, args.dry_run)
            count = len(report["labelled"])
            if not count and not report["skipped"]:
                human = "nothing to backfill"
            else:
                verb = "would label" if args.dry_run else "labelled"
                human = (
                    f"{verb} {count} issues, "
                    f"{'would create' if args.dry_run else 'created'} "
                    f"{len(report['created_labels'])} labels, skipped {len(report['skipped'])}"
                )
            _render_result(report, human, args.as_json)
            return 0
        if args.command == "area":
            validate_area(
                args.name,
                DEFAULT_COLOR if args.color is None else args.color,
                DEFAULT_DESCRIPTION if args.description is None else args.description,
            )
            repository = resolve_repository(args.repo)
            use_repository(repository)
            created = ensure_area(repository, args.name, args.color, args.description)
            action = "created" if created else "unchanged"
            _render_result(
                {"action": action, "area": args.name, "schema_version": 1},
                f"{action} area {args.name}",
                args.as_json,
            )
            return 0
        if args.command == "init" and args.mode is None:
            raise QewError("init requires --mode overlay or --mode repository", 2)
        repository = resolve_repository(args.repo)
        use_repository(repository)
        if args.command == "prime":
            context = render_prime(fetch_prime_graph(repository))
            if args.as_json:
                print(
                    json.dumps(
                        prime_json(context),
                        separators=(",", ":"),
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                )
            else:
                print(context)
            return 0
        if args.command == "ready":
            if args.limit < 0:
                raise QewError("ready limit must be zero or greater", 2)
            all_items = derive_ready(fetch_ready_graph(repository))
            items = all_items if args.limit == 0 else all_items[: args.limit]
            if len(items) < len(all_items):
                print(
                    f"warning: ready output truncated to {len(items)} of {len(all_items)} items",
                    file=sys.stderr,
                )
            if args.as_json:
                print(
                    json.dumps(
                        ready_json(items, len(all_items)),
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            else:
                for item in items:
                    print(
                        f"#{item.number} {item.priority.value} "
                        f"{item.work_type.value}  {item.title}"
                    )
            return 0
        if args.command == "plan" and args.plan_command == "status":
            statuses = derive_plan_statuses(fetch_plan_status_graph(repository), args.issue)
            if args.as_json:
                _render_ndjson(statuses)
            else:
                for status in statuses:
                    counts = status["children"]
                    print(
                        f"#{status['number']} {status['title']}"
                        f"{' (draft plan)' if status['draft'] else ''}: "
                        f"{counts['closed']}/{counts['total']} closed"
                        + _plural_drafts(counts["draft"])
                    )
                    next_item = status["next"]
                    if next_item is None:
                        print("next: none (no ready child)")
                    else:
                        print(
                            f"next: #{next_item['number']} {next_item['priority']} "
                            f"{next_item['title']} ({next_item['reason']})"
                        )
                    if status["completable"]:
                        print(f"complete: {status['complete_command']}")
            return 0
        if args.command == "plan" and args.plan_command == "complete":
            changed = complete_plan(repository, args.issue, args.reason)
            output = {
                "changed": changed,
                "issue": args.issue,
                "next_command": "qew campaign status",
                "schema_version": 1,
                "state": "completed",
            }
            renderer = _render_mutation if changed else _render_result
            renderer(
                output,
                f"{'completed' if changed else 'unchanged'} plan #{args.issue}",
                args.as_json,
            )
            if not args.as_json:
                print("next: qew campaign status")
            return 0
        if args.command == "plan" and args.plan_command == "create":
            children = _issue_numbers(args.children, "children")
            require_quote(args.quote)  # refuse before the editor opens
            body_text = edit_body() if args.edit else None
            created = create_plan(
                PlanCreateRequest(
                    repository=repository,
                    title=args.title,
                    priority=args.priority,
                    children=children,
                    goal=args.goal,
                    done_when=args.done_when,
                    body_file=args.body_file,
                    body_text=body_text,
                    quote=args.quote,
                    draft=args.draft,
                )
            )
            _render_mutation(
                {**created, "schema_version": 1},
                f"created plan #{created['number']} with {len(children)} children",
                args.as_json,
                _project_sync(repository, [created["number"]]),
            )
            return 0
        if args.command == "list":
            items = list_items(
                fetch_vetted_issues(repository, bodies=args.bodies),
                state=args.state,
                labels=args.label or (),
                author=args.author,
                assignee=args.assignee,
                epic=args.epic,
                bodies=args.bodies,
            )
            _render_rows(items, args.as_json)
            return 0
        if args.command == "inbox":
            if args.count and args.as_json:
                raise QewError("--count and --json cannot be combined", 2)
            since = since_or_default(args.since)
            inbox = build_inbox(repository, args.login, since)
            if args.count:
                print(inbox_count_line(inbox))
            elif args.as_json:
                _render_ndjson(inbox_rows(inbox))
            else:
                print("\n".join(inbox_text_lines(inbox)))
            return 0
        if args.command == "draft" and args.draft_command == "list":
            items = draft_items(fetch_draft_issues(repository), args.parent)
            if args.as_json:
                _render_result({"items": items, "schema_version": 1}, "", True)
            else:
                for item in items:
                    kind = " ".join(filter(None, (item["priority"], item["type"])))
                    under = "" if item["parent"] is None else f" (under #{item['parent']})"
                    print(f"#{item['number']} {kind} {item['title']}{under}")
            return 0
        if args.command == "draft" and args.draft_command == "approve":
            result = approve_drafts(repository, args.issues, args.tree, args.quote)
            _render_mutation(
                {
                    "approved": result.moved,
                    "schema_version": 1,
                    "skipped": result.skipped,
                    "unrecorded": [number for number, _ in result.unrecorded],
                },
                _moved_line("approved", result),
                args.as_json,
                _project_sync(repository, result.moved),
            )
            _warn_unrecorded(result)
            return 0
        if args.command == "draft" and args.draft_command == "revert":
            result = revert_to_draft(
                repository, args.issues, args.tree, args.reason or "", args.quote
            )
            _render_mutation(
                {
                    "reverted": result.moved,
                    "schema_version": 1,
                    "skipped": result.skipped,
                    "unrecorded": [number for number, _ in result.unrecorded],
                },
                _moved_line("reverted", result) + " to draft",
                args.as_json,
                _project_sync(repository, result.moved),
            )
            _warn_unrecorded(result)
            return 0
        if args.command == "comment":
            add_comment(repository, args.issue, args.body, args.body_file)
            _render_result(
                {"issue": args.issue, "schema_version": 1}, f"commented on #{args.issue}", args.as_json
            )
            return 0
        if args.command == "show":
            payload = fetch_show_issue(repository, args.issue)
            item = show_item(payload)
            item["claim"] = _shown_claim(repository, item)
            if args.as_json:
                print(json.dumps(item, separators=(",", ":"), sort_keys=True))
            else:
                state = f"{item['state']} draft" if item["draft"] else item["state"]
                print(f"#{item['number']} {state}  {item['title']}")
                _render_text_findings(item)
                print(item["body"])
                claim = item["claim"]
                if claim is not None:
                    where = "" if claim["workspace"] is None else f" (workspace {claim['workspace']})"
                    print(f"claim: lane {claim['lane']} by {claim['by']}{where}")
                if item["blocked_by"]:
                    blockers = ", ".join(f"#{value['number']}" for value in item["blocked_by"])
                    print(f"blocked by: {blockers}")
                if item["children"]:
                    children = ", ".join(f"#{value['number']}" for value in item["children"])
                    print(f"children: {children}")
                if item["next"] is not None:
                    value = item["next"]
                    print(f"next: #{value['number']} {value['priority']} {value['title']}")
            return 0
        if args.command == "search":
            terms = " ".join(args.terms).strip()
            if not terms:
                raise QewError("search terms must not be empty", 2)
            payload = search_vetted_issues(repository, terms)
            items = search_items(payload)
            _render_rows(items, args.as_json)
            if search_is_capped(payload):
                print("warning: search results capped; refine the search terms", file=sys.stderr)
            if not items:
                print(f"no vetted matches; also run qew triage --search {json.dumps(terms)}", file=sys.stderr)
            return 0
        if args.command == "triage":
            items = triage_items(fetch_triage_issues(repository), args.search)
            if args.as_json:
                _render_ndjson(items)
            else:
                for item in items:
                    line = (
                        f"#{item['number']} {item['state']} "
                        f"missing {','.join(item['missing'])}  {item['title']}"
                    )
                    findings = item.get("text_findings", [])
                    if findings:
                        annotations = []
                        for finding in findings:
                            detail = "; ".join(_text_finding_parts(finding))
                            annotations.append(f"{finding['source']}: {detail}")
                        line += "  [" + "; ".join(annotations) + "]"
                    print(line)
            return 0
        if args.command == "blocked":
            items = blocked_items(fetch_blocked_issues(repository))
            if args.as_json:
                _render_ndjson(items)
            else:
                for item in items:
                    blockers = ",".join(f"#{value['number']}" for value in item["blocked_by"])
                    print(f"#{item['number']} {item['priority']} {item['type']} <- {blockers}  {item['title']}")
            return 0
        if args.command == "init":
            result = initialize(repository, RepositoryMode(args.mode), args.project)
            if args.as_json:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            else:
                print(f"initialized {repository} ({len(result['changed'])} labels changed)")
                print(result["guidance"])
            return 0
        if args.command == "campaign" and args.campaign_command == "create":
            result = create_campaign(
                repository,
                title=args.title,
                description=args.description,
                due_on=args.due_on,
                plans=args.plan,
                quote=args.quote,
            )
            if args.as_json:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            else:
                print(f"campaign {result['title']} {result['url']}")
            return 0
        if args.command == "campaign" and args.campaign_command == "complete":
            result = complete_campaign(repository, args.title)
            _render_result(
                result,
                f"{'completed' if result['changed'] else 'unchanged'} campaign {result['title']}",
                args.as_json,
            )
            return 0
        if args.command == "campaign" and args.campaign_command == "status":
            statuses = campaign_statuses(repository, args.title)
            if args.as_json:
                _render_ndjson(statuses)
            else:
                for status in statuses:
                    line = (
                        f"#{status['milestone']} {render_campaign_title(status)}: "
                        f"{status['closed_issues']} closed, {status['open_issues']} open"
                    )
                    print(line)
                    if status["complete_command"]:
                        print(f"complete: {status['complete_command']}")
            return 0
        if args.command == "project" and args.project_command == "ensure":
            result = ensure_project(repository, args.title)
            if args.as_json:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            else:
                print(f"project {result['title']} {result['url']}")
            return 0
        if args.command == "project" and args.project_command == "prune":
            result = prune_project(repository, args.source, args.title)
            _render_result(
                result,
                f"pruned {result['removed']} cards of {result['from']} from {result['title']}",
                args.as_json,
            )
            return 0
        if args.command == "project" and args.project_command == "status":
            result = project_status(repository, args.title)
            if args.as_json:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            else:
                print(_render_project_status(result))
            return 0
        if args.command == "graph" and args.graph_command == "check":
            report = graph_report(fetch_dependency_graph(repository))
            if args.as_json:
                print(json.dumps(report, separators=(",", ":"), sort_keys=True))
            elif report["valid"]:
                print("graph valid")
            else:
                print(
                    f"graph invalid: {len(report['cycles'])} cycles, "
                    f"{len(report['contradictions'])} contradictions"
                )
            return 0 if report["valid"] else 2
        if args.command == "start":
            if args.take_over and args.reason is None:
                raise QewError("--take-over requires --reason", 2)
            if args.reason is not None and not args.take_over:
                raise QewError("--reason is only valid with --take-over", 2)
            claim = start(
                repository,
                args.issue,
                args.priority,
                args.lane,
                args.workspace,
                args.kind,
                load_config().max_active_claims,
                args.reason if args.take_over else None,
            )
            started = {"issue": args.issue, "schema_version": 1, "state": "started"}
            human = f"started #{args.issue}"
            if claim is not None:
                started.update({"kind": claim.kind, "lane": claim.lane, "workspace": claim.workspace})
                human = f"started #{args.issue} for lane {claim.lane}"
            _render_mutation(
                started,
                human,
                args.as_json,
                _project_sync(repository, [args.issue]),
            )
            return 0
        if args.command == "land":
            report = land(repository, args.pull_request)
            lines = render_land(report)
            output = {
                "clean": report.clean,
                "held": [{"issue": issue, "by": owners} for issue, owners in sorted(report.held.items())],
                "landed": report.landed,
                "merged": report.merged,
                "open": report.open_issues,
                "pull_request": report.pull_request,
                "schema_version": 1,
                "state": "landed" if report.landed else "unchanged",
            }
            if report.landed:
                _render_mutation(
                    output,
                    "\n".join(lines),
                    args.as_json,
                    _project_sync(repository, report.landed, dependents=True),
                )
            else:
                _render_result(output, "\n".join(lines), args.as_json)
            return 0
        if args.command == "release":
            freed = release(repository, args.issue, args.reason, force=args.force, lane=args.lane)
            lane_text = "no lane" if freed.lane is None else f"lane {freed.lane}"
            _render_mutation(
                {
                    "by": freed.by,
                    "issue": args.issue,
                    "lane": freed.lane,
                    "schema_version": 1,
                    "state": "released",
                },
                f"released #{args.issue} ({lane_text})",
                args.as_json,
                _project_sync(repository, [args.issue]),
            )
            return 0
        if args.command == "complete":
            evidence = completion_evidence(args.commit, args.landed_on, args.no_commit)
            complete(repository, args.issue, args.reason, evidence, args.take_over)
            _render_mutation(
                {"issue": args.issue, "schema_version": 1, "state": "completed"},
                f"completed #{args.issue}",
                args.as_json,
                _project_sync(repository, [args.issue], dependents=True),
            )
            return 0
        if args.command == "close":
            disposition = close_work(
                repository,
                args.issue,
                args.reason,
                completed=args.completed,
                abandoned=args.abandoned,
                duplicate_of=args.duplicate_of,
                delivered_by=args.delivered_by,
                landed_on=args.landed_on,
                superseded_by=args.superseded_by,
            )
            _render_mutation(
                {
                    "disposition": disposition,
                    "issue": args.issue,
                    "schema_version": 1,
                    "state": "closed",
                },
                f"closed #{args.issue} as {disposition}",
                args.as_json,
                _project_sync(repository, [args.issue], dependents=True),
            )
            return 0
        if args.command == "reopen":
            changed = reopen(repository, args.issue, args.reason)
            human = (
                f"reopened #{args.issue}"
                if changed
                else f"unchanged #{args.issue} (already open)"
            )
            output = {
                "changed": changed,
                "issue": args.issue,
                "schema_version": 1,
                "state": "reopened" if changed else "open",
            }
            if changed:
                _render_mutation(
                    output,
                    human,
                    args.as_json,
                    _project_sync(repository, [args.issue], dependents=True),
                )
            else:
                _render_result(output, human, args.as_json)
            return 0
        if args.command == "pr" and args.pr_command == "merge":
            outcome = merge_pull_request_command(
                repository,
                args.number,
                auto=args.auto,
                admin=args.admin,
                reason=args.reason,
                other_author=args.other_author,
            )
            landed = [] if outcome.landing is None else outcome.landing.landed
            retry = f"qew land {args.number} --repo {repository}"
            lines = []
            if args.admin:
                lines.append(f"admin merge: {args.reason}")
            if args.other_author:
                lines.append(f"merging {outcome.author}'s pull request: {args.reason}")
            if outcome.merged:
                lines.append(f"merged PR #{args.number}")
                if outcome.landing is not None:
                    lines.extend(render_land(outcome.landing))
            else:
                lines.append(f"auto-merge enabled for PR #{args.number}; after it merges run: {retry}")
            _render_result(
                {
                    "auto": outcome.auto,
                    "landed": landed,
                    "landing_error": outcome.landing_error,
                    "merged": outcome.merged,
                    "pull_request": args.number,
                    "schema_version": 1,
                },
                "\n".join(lines),
                args.as_json,
            )
            if outcome.landing_error is not None:
                print(f"landing failed: {outcome.landing_error}; run: {retry}", file=sys.stderr)
                return 3
            if landed:
                _project_sync(repository, landed, dependents=True)
            return 0
        if args.command == "pr" and args.pr_command == "check":
            modes = [bool(args.items), args.all_items, args.list_items]
            if sum(modes) != 1:
                raise QewError("pass ITEM... or --all or --list (exactly one)", 2)
            if args.list_items:
                items = checklist.read(repository, args.number)
                rows = [{"checked": item.checked, "id": item.id, "text": item.text} for item in items]
                human = "\n".join(
                    f"[{'x' if row['checked'] else ' '}] {row['id']}  {row['text']}" for row in rows
                ) or f"PR #{args.number} has no checklist"
                _render_result(
                    {"items": rows, "pull_request": args.number, "schema_version": 1}, human, args.as_json
                )
                return 0
            outcome = checklist.tick(repository, args.number, args.items, args.all_items)
            human = (
                f"ticked {', '.join(outcome['changed'])}; open: {', '.join(outcome['open']) or 'none'}"
                if outcome["changed"]
                else f"nothing to tick; open: {', '.join(outcome['open']) or 'none'}"
            )
            _render_result(
                {**outcome, "pull_request": args.number, "schema_version": 1}, human, args.as_json
            )
            return 0
        if args.command == "pr" and args.pr_command == "ready":
            changed = mark_pull_request_ready_for_review(repository, args.number)
            _render_result(
                {
                    "changed": changed,
                    "pull_request": args.number,
                    "schema_version": 1,
                    "state": "ready",
                },
                f"marked PR #{args.number} ready"
                if changed
                else f"PR #{args.number} is already ready",
                args.as_json,
            )
            return 0
        if args.command == "pr":
            created = create_pull_request(
                PullRequest(
                    repository=repository,
                    issue=args.issue,
                    title=args.title,
                    what=args.what,
                    why=args.why,
                    testing=args.testing,
                    body_file=args.body_file,
                    base=args.base,
                    ready=args.ready,
                    head=args.head,
                ),
                _warn_code_shaped_text,
            )
            state = "draft PR" if created["draft"] else "PR"
            _render_mutation(
                {**created, "schema_version": 1},
                f"created {state} #{created['number']} for #{created['issue']}",
                args.as_json,
                _project_sync(
                    repository,
                    [created["issue"]],
                    awaited_pull_requests={created["issue"]: created["number"]},
                ),
            )
            return 0
        if args.command == "create":
            if args.approved and args.needs_approval:
                raise QewError("--approved and --needs-approval contradict each other", 2)
            if args.draft and (args.approved or args.needs_approval):
                raise QewError("--draft cannot be combined with --approved or --needs-approval", 2)
            require_quote(args.quote)  # refuse before the editor opens
            body_text = edit_body() if args.edit else None
            created = create(
                CreateRequest(
                    repository=repository,
                    issue_type=args.type,
                    title=args.title,
                    priority=args.priority,
                    areas=args.area,
                    parent=args.parent,
                    blocked_by=args.blocked_by,
                    discovered_from=args.discovered_from,
                    where=args.where,
                    problem=args.problem,
                    goal=args.goal,
                    fix=args.fix,
                    approach=args.approach,
                    done_when=args.done_when,
                    body_file=args.body_file,
                    quote=args.quote,
                    needs_approval=not args.approved and not args.draft,
                    body_text=body_text,
                    draft=args.draft,
                ),
                _warn_code_shaped_text,
            )
            output = {**created, "schema_version": 1}
            _render_mutation(
                output,
                f"created #{created['number']} {created['url']}",
                args.as_json,
                _project_sync(repository, [created["number"]]),
            )
            return 0
        if args.command == "set":
            changed = set_issue(
                SetRequest(
                    repository=repository,
                    issue=args.issue,
                    priority=args.priority,
                    work_type=args.work_type,
                    add_areas=args.add_area,
                    remove_areas=args.remove_area,
                    parent=args.parent,
                    no_parent=args.no_parent,
                    title=args.title,
                    body_file=args.body_file,
                    allow_closed=args.closed,
                    needs_approval=args.needs_approval,
                )
            )
            output = {"changed": changed, "issue": args.issue, "schema_version": 1}
            human = f"{'updated' if changed else 'unchanged'} #{args.issue}"
            linked = changed and linked_project(repository) is not None
            if linked:
                # A linked board is synced for any change: labels such as
                # needs:approval move the card even without a priority or type edit.
                _render_mutation(output, human, args.as_json, _project_sync(repository, [args.issue]))
            elif changed and (args.priority is not None or args.work_type is not None):
                _render_mutation(output, human, args.as_json)
            else:
                _render_result(output, human, args.as_json)
            return 0
        if args.command == "block":
            block(repository, args.issue, args.blocker, allow_closed=args.closed)
            output = {"blocked_by": args.blocker, "issue": args.issue, "schema_version": 1}
            _render_mutation(
                output,
                f"blocked #{args.issue} on #{args.blocker}",
                args.as_json,
                _project_sync(repository, [args.issue]),
            )
            return 0
        if args.command == "unblock":
            unblock(repository, args.issue, args.blocker, allow_closed=args.closed)
            output = {"issue": args.issue, "schema_version": 1, "unblocked_from": args.blocker}
            _render_mutation(
                output,
                f"unblocked #{args.issue} from #{args.blocker}",
                args.as_json,
                _project_sync(repository, [args.issue]),
            )
            return 0
        if args.command == "apply":
            result = apply_plan(
                repository,
                args.plan,
                args.state,
                args.dry_run,
                parse_throttle(args.throttle),
                args.draft,
            )
            if args.dry_run and args.as_json:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            elif args.dry_run:
                for operation in result["operations"]:
                    print(json.dumps(operation, separators=(",", ":"), sort_keys=True))
            else:
                human = f"applied {len(result['created'])} work items"
                if result.get("projects"):
                    _render_result(result, human, args.as_json)
                else:
                    _render_mutation(result, human, args.as_json)
            return 0
    except QewError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code
    return 2


def _issue_numbers(value: Optional[str], name: str):
    if value is None:
        return []
    parts = value.split(",")
    if any(not (part.strip().isascii() and part.strip().isdigit()) for part in parts):
        raise QewError(f"{name} must be comma-separated positive issue numbers", 2)
    numbers = [int(part.strip()) for part in parts]
    if any(number <= 0 for number in numbers):
        raise QewError(f"{name} must be comma-separated positive issue numbers", 2)
    return numbers


def _render_rows(items, as_json: bool) -> None:
    if as_json:
        _render_ndjson(items)
        return
    for item in items:
        line = (
            f"#{item['number']} {item['priority']} {item['type']} "
            f"{item['state']}  {item['title']}"
        )
        work_state = item.get("work_state")
        if work_state is not None and work_state != "ready":
            line += f"  [{work_state}]"
        print(line)


def _render_project_status(result) -> str:
    lines = []
    if result["in_sync"]:
        lines.append(f"project {result['title']} in sync")
    else:
        lines.append(f"project {result['title']} has drifted:")
    for label, key in (
        ("missing from the Project", "missing"),
        ("stale status", "stale_status"),
        ("stale priority", "stale_priority"),
        ("closed but still claimed", "stale_claims"),
    ):
        numbers = result[key]
        if numbers:
            shown = ", ".join(f"#{number}" for number in numbers[:20])
            more = f" (+{len(numbers) - 20} more)" if len(numbers) > 20 else ""
            lines.append(f"  {label}: {shown}{more}")
    if result["fix"] is not None:
        lines.append(f"fix: {result['fix']}")
    if result["claim_fix"] is not None:
        lines.append(f"claim fix: {result['claim_fix']}")
    return "\n".join(lines)


def _render_ndjson(items) -> None:
    for item in items:
        print(json.dumps(item, separators=(",", ":"), sort_keys=True))


def _render_result(value, human: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, separators=(",", ":"), sort_keys=True))
    else:
        print(human)


def _run_hook(event: str, agent: str) -> int:
    """Answer an agent's shell-command hook. Never blocks, never fails, silent off-Qew."""
    try:
        load_config()
        text = hook_message(event, sys.stdin.read(), _land_for_hook)
    except Exception:  # a hook must not break the agent's session
        text = None
    output = hook_render(agent, event, text)
    if output:
        print(output)
    return 0


def _land_for_hook(repository: Optional[str], pull_request: int) -> str:
    try:
        repository = resolve_repository(repository)
        report = land(repository, pull_request)
        if report.landed:
            _project_sync(repository, report.landed, dependents=True)
        return "\n".join(render_land(report))
    except Exception as error:
        return (
            f"qew land {pull_request} failed: {error}; "
            f"after the merge run: qew land {pull_request}"
        )


def _moved_line(verb: str, result) -> str:
    line = f"{verb} " + ", ".join(f"#{number}" for number in result.moved)
    if result.skipped:
        line += "; skipped " + ", ".join(f"#{number}" for number in result.skipped)
    return line


def _warn_unrecorded(result) -> None:
    for note in result.notes:
        print(f"warning: {note}", file=sys.stderr)
    for number, text in result.unrecorded:
        print(
            f"warning: the comment for #{number} was not recorded; add it with: "
            f"qew comment {number} --body {shlex.quote(text)}",
            file=sys.stderr,
        )


def _plural_drafts(count: int) -> str:
    return "" if count == 0 else f", {count} draft{'s' if count != 1 else ''}"


def _project_sync(repository: str, issues, dependents: bool = False, awaited_pull_requests=None):
    """Move the affected cards on the linked board.

    Returns the sync report, or None when no board is linked or the sync failed.
    A failed sync never fails the graph write: the Project is derived
    visibility, and a non-zero exit would invite a retry of a non-idempotent
    command. The reconciliation hint stays in place instead.
    """
    try:
        project = linked_project(repository)
        # Epics whose derived claim just changed move with the issues that caused it.
        issues = [*issues, *take_changed_epics()]
        if project is None:
            return None
        return sync_cards(repository, project, issues, dependents, awaited_pull_requests)
    except QewError as error:
        detail = str(error)
        if detail.startswith("indeterminate: missing ") and " option " in detail:
            detail += "; run qew project ensure to add it"
        print(f"warning: project sync failed: {detail}", file=sys.stderr)
        return None


def _shown_claim(repository: str, item):
    if item["state"] != "open" or "in-progress" not in item["labels"] or not item["assignees"]:
        return None
    claim = issue_claim(repository, item["number"], item["assignees"])
    return None if claim is None else claim.as_json()


def _render_mutation(value, human: str, as_json: bool, sync=None) -> None:
    if sync is not None:
        _render_result({**value, "project_sync": sync} if as_json else value, human, as_json)
        return
    reconciliation = {
        "command": "qew project ensure --title TITLE",
        "reason": "GitHub Projects are derived visibility and may now be stale.",
    }
    if as_json:
        _render_result(
            {**value, "project_reconciliation": reconciliation},
            human,
            True,
        )
    else:
        _render_result(value, human, False)
    print(
        "hint: GitHub Projects are derived visibility; refresh when useful with: "
        + reconciliation["command"],
        file=sys.stderr,
    )


def _render_text_findings(item) -> None:
    findings = item.get("text_findings", [])
    if not findings:
        return
    print("text findings:")
    for finding in findings:
        source = finding["source"]
        if source == "comment":
            index = finding["comment_index"]
            author = item["comments"][index]["author"]
            source = f"comment {index + 1} (@{author})"
        print(f"  {source}: {'; '.join(_text_finding_parts(finding))}")


def _warn_code_shaped_text(body: str) -> None:
    for token in unmarked_code_tokens(body):
        print(f"warning: unmarked code-shaped text: {token}", file=sys.stderr)


def _text_finding_parts(finding) -> list:
    labels = (
        ("zero_width", "contains zero-width characters"),
        ("bidi_control", "contains bidi controls"),
        ("unicode_tags", "contains Unicode tags"),
        ("confusable", "contains confusable characters"),
    )
    return [label for key, label in labels if finding.get(key)]


if __name__ == "__main__":
    raise SystemExit(main())
