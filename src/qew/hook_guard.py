"""Find shell commands that route around the ledger, without ever executing them.

Command text comes from an agent and is data: it is tokenized the way a shell
would, split into simple commands at operators, and matched structurally against
a rule table. A quoted example such as `echo "gh issue edit 3"` is one argument,
never a command. `sh -c STRING` is unwrapped (a few levels) because it is the
cheapest way to hide a command from a purely lexical match.

Known gaps: wrappers that take a value (`sudo -u root gh ...`), `eval`, scripts
run from files, and a variable holding the command. The guard stops the direct
and the obvious indirect forms; it is a guard rail, not a sandbox.
"""

from dataclasses import dataclass
import re
import shlex
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

_ENVIRONMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = frozenset({"sudo", "env", "command", "exec", "nohup", "time", "builtin"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_SHELL_COMMAND_FLAG = re.compile(r"-[a-z]*c[a-z]*")
_MAX_NESTING = 3
# Global flags of the forge CLIs that take a value and may precede the subcommand.
_GLOBAL_VALUE_FLAGS = frozenset({"-R", "--repo", "-r", "-l", "--login", "--hostname", "-C"})

_MERGE = "`qew pr merge PR`"
_CLOSE = "`qew complete ITEM` (finished work) or `qew close ITEM --reason R` (abandoned, duplicate, superseded)"
_ISSUE_ADVICE: Mapping[str, str] = {
    "create": "`qew create --title T` (a draft until a human approves)",
    "edit": "`qew set ITEM` (title, labels, priority, body)",
    "update": "`qew set ITEM` (title, labels, priority, body)",
    "close": _CLOSE,
    "reopen": "`qew reopen ITEM`",
}
_ISSUE_DEFAULT = "`qew set ITEM`, `qew close ITEM --reason R` or `qew sync` (the forge mirror follows the ledger)"
_LABEL_ADVICE = "`qew set ITEM --labels ...` (labels live on the item; `qew sync` mirrors them)"
_BOARD_ADVICE = "`qew project ensure` (board) and `qew sync` (cards follow the ledger)"
_MILESTONE_ADVICE = "`qew set ITEM --parent EPIC` (an epic is the milestone; `qew sync` mirrors it)"
_API_ADVICE = "the matching qew command (`qew create`, `qew set`, `qew complete`, `qew project ensure`, `qew sync`)"


@dataclass(frozen=True)
class Refusal:
    """A command that bypasses qew, and the qew command to use instead."""

    command: str
    advice: str

    def reason(self) -> str:
        return (
            f"Blocked `{self.command}`: work in this repository is managed by the Qew ledger, "
            f"and forge changes go through it. Use {self.advice} instead."
        )


@dataclass(frozen=True)
class _Area:
    """One subcommand area of a forge CLI and which of its actions write.

    `writes` set: only those actions are refused. `reads` set: every action but
    those is refused. Neither: any use of the area is refused. A bare area (no
    action) is never refused: it only prints help or a listing.
    """

    names: FrozenSet[str]
    advice: Mapping[str, str]
    default_advice: str
    writes: Optional[FrozenSet[str]] = None
    reads: Optional[FrozenSet[str]] = None
    bare_is_write: bool = False
    exempt_flags: FrozenSet[str] = frozenset()

    def refused(self, action: Optional[str], flags: FrozenSet[str]) -> bool:
        if flags & self.exempt_flags:
            return False
        if action is None:
            return self.bare_is_write
        if self.writes is not None:
            return action in self.writes
        if self.reads is not None:
            return action not in self.reads
        return True


def _fs(*names: str) -> FrozenSet[str]:
    return frozenset(names)


_PR_MERGE = {"merge": _MERGE}
_CLI_RULES: Dict[str, Tuple[_Area, ...]] = {
    "gh": (
        _Area(_fs("issue"), _ISSUE_ADVICE, _ISSUE_DEFAULT,
              writes=_fs("create", "edit", "close", "reopen", "comment", "delete", "transfer", "pin", "unpin", "lock", "unlock")),
        _Area(_fs("label"), {}, _LABEL_ADVICE, reads=_fs("list")),
        _Area(_fs("project"), {}, _BOARD_ADVICE, reads=_fs("list", "view", "field-list", "item-list")),
        _Area(_fs("pr"), _PR_MERGE, _MERGE, writes=_fs("merge"), exempt_flags=_fs("--disable-auto")),
    ),
    "glab": (
        _Area(_fs("issue"), _ISSUE_ADVICE, _ISSUE_DEFAULT,
              writes=_fs("create", "update", "close", "reopen", "delete", "note", "subscribe", "unsubscribe")),
        _Area(_fs("label"), {}, _LABEL_ADVICE, reads=_fs("list", "get")),
        _Area(_fs("milestone"), {}, _MILESTONE_ADVICE, reads=_fs("list", "get")),
        _Area(_fs("mr"), _PR_MERGE, _MERGE, writes=_fs("merge")),
    ),
    "tea": (
        _Area(_fs("issues", "issue", "i"), _ISSUE_ADVICE, _ISSUE_DEFAULT,
              writes=_fs("create", "edit", "close", "reopen")),
        _Area(_fs("labels", "label", "l"), {}, _LABEL_ADVICE, reads=_fs("list")),
        _Area(_fs("milestones", "milestone", "ms"), {}, _MILESTONE_ADVICE, reads=_fs("list", "issues")),
        _Area(_fs("pulls", "pull", "pr"), _PR_MERGE, _MERGE, writes=_fs("merge")),
        _Area(_fs("comment", "c"), {}, _ISSUE_DEFAULT, bare_is_write=True),
    ),
    "fj": (
        _Area(_fs("issue"), _ISSUE_ADVICE, _ISSUE_DEFAULT,
              writes=_fs("create", "edit", "close", "reopen", "comment", "delete")),
        _Area(_fs("pr"), _PR_MERGE, _MERGE, writes=_fs("merge")),
    ),
}
_API_BINARIES = _fs("gh", "glab")

# API paths (forge-neutral: GitHub, GitLab, Gitea and Forgejo spell them alike).
_TRACKER_PATH = re.compile(r"/(issues|labels|milestones|boards)(/|$)")
_BOARD_PATH = re.compile(r"^/(orgs/[^/]+|users/[^/]+|user|repos/[^/]+/[^/]+)/projects(/|$)")
_MERGE_PATH = re.compile(r"/(pulls|pull|merge_requests)/[0-9]+/merge$")
_GRAPHQL_PATH = re.compile(r"(^|/)graphql$")
_MUTATION = re.compile(r"\bmutation\b", re.IGNORECASE)
_MUTATION_TARGET = re.compile(r"issue|label|project|milestone|comment|mergepullrequest", re.IGNORECASE)
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_API_VALUE_FLAGS = _fs("-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "--input",
                       "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview", "-R", "--repo")
_API_FIELD_FLAGS = _fs("-f", "--raw-field", "-F", "--field")

_CURL_VALUE_FLAGS = _fs("-X", "--request", "-d", "--data", "--data-raw", "--data-binary", "--data-ascii",
                        "--data-urlencode", "-F", "--form", "-T", "--upload-file", "--json", "-H", "--header",
                        "-o", "--output", "-u", "--user", "-A", "--user-agent", "-e", "--referer", "--url")
_CURL_BODY_FLAGS = _fs("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode",
                       "-F", "--form", "-T", "--upload-file", "--json")
_WGET_BODY_FLAGS = _fs("--post-data", "--post-file", "--body-data", "--body-file")
_HTTPIE = _fs("http", "https", "xh", "xhs")
_HTTP_METHOD = re.compile(r"[A-Z]{3,}")
_HTTPIE_HOST = re.compile(r"(https?://)?[\w.-]+(:\d+)?(/.*)?")


def refusals(command: str) -> List[Refusal]:
    found: List[Refusal] = []
    for words in _simple_commands(command, 0):
        found.extend(_classify(words))
    unique: List[Refusal] = []
    for refusal in found:
        if refusal not in unique:
            unique.append(refusal)
    return unique


def _segments(command: str) -> List[List[str]]:
    segments: List[List[str]] = []
    for line in command.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            tokens = list(lexer)
        except ValueError:
            continue
        current: List[str] = []
        for token in tokens:
            if token and all(character in "();<>|&" for character in token):
                if current:
                    segments.append(current)
                current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
    return segments


def _simple_commands(command: str, depth: int) -> List[List[str]]:
    commands: List[List[str]] = []
    for segment in _segments(command):
        words = _strip_prefix(segment)
        if not words:
            continue
        commands.append(words)
        if depth < _MAX_NESTING and _basename(words[0]) in _SHELLS:
            script = _shell_script(words[1:])
            if script is not None:
                commands.extend(_simple_commands(script, depth + 1))
    return commands


def _strip_prefix(tokens: List[str]) -> List[str]:
    words = list(tokens)
    while words:
        if _ENVIRONMENT.match(words[0]):
            words.pop(0)
        elif _basename(words[0]) in _WRAPPERS:
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
        else:
            break
    return words


def _shell_script(arguments: List[str]) -> Optional[str]:
    for position, argument in enumerate(arguments):
        if _SHELL_COMMAND_FLAG.fullmatch(argument):
            return arguments[position + 1] if position + 1 < len(arguments) else None
        if not argument.startswith("-"):
            return None
    return None


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _classify(words: List[str]) -> List[Refusal]:
    binary = _basename(words[0])
    if binary in _CLI_RULES:
        found = _cli(binary, words[1:])
        if binary in _API_BINARIES:
            found.extend(_cli_api(binary, words[1:]))
        return found
    if binary == "curl":
        return _http(words, "curl")
    if binary == "wget":
        return _http(words, "wget")
    if binary in _HTTPIE:
        return _http(words, "httpie")
    return []


def _subcommand(arguments: List[str]) -> Tuple[List[str], FrozenSet[str]]:
    """The words after the CLI's global flags, and every flag seen."""
    words: List[str] = []
    flags = set()
    skip = False
    for argument in arguments:
        if skip:
            skip = False
        elif argument.startswith("-"):
            flags.add(argument.partition("=")[0])
            skip = argument in _GLOBAL_VALUE_FLAGS
        else:
            words.append(argument)
    return words, frozenset(flags)


def _cli(binary: str, arguments: List[str]) -> List[Refusal]:
    words, flags = _subcommand(arguments)
    if not words:
        return []
    area_name = words[0]
    action = words[1] if len(words) > 1 else None
    for area in _CLI_RULES[binary]:
        if area_name in area.names and area.refused(action, flags):
            label = f"{binary} {area_name}" + (f" {action}" if action else "")
            return [Refusal(label, area.advice.get(action or "", area.default_advice))]
    return []


def _cli_api(binary: str, arguments: List[str]) -> List[Refusal]:
    if not arguments or arguments[0] != "api":
        return []
    method: Optional[str] = None
    fields = False
    opaque_input = False
    endpoint: Optional[str] = None
    skip: Optional[str] = None
    for argument in arguments[1:]:
        if skip is not None:
            if skip in ("-X", "--method"):
                method = argument.upper()
            elif skip in _API_FIELD_FLAGS:
                fields = True
            elif skip == "--input":
                opaque_input = True
            skip = None
            continue
        name, equals, value = argument.partition("=")
        if argument.startswith("-"):
            if name in _API_VALUE_FLAGS and not equals:
                skip = name
                continue
            if name in ("-X", "--method") and equals:
                method = value.upper()
            elif name in _API_FIELD_FLAGS and equals:
                fields = True
            elif name == "--input" and equals:
                opaque_input = True
            elif argument.startswith("-X") and len(argument) > 2:
                method = argument[2:].upper()
        elif endpoint is None:
            endpoint = argument
    if endpoint is None:
        return []
    body = fields or opaque_input
    writes = method in _WRITE_METHODS or (method is None and body)
    if not writes:
        return []
    label = f"{binary} api {method or 'POST'} {endpoint}"
    path = "/" + endpoint.split("?", 1)[0].strip("/")
    if _GRAPHQL_PATH.search(path):
        return _graphql(label, arguments, opaque_input)
    return _path_refusal(label, path)


def _path_refusal(label: str, path: str) -> List[Refusal]:
    if _MERGE_PATH.search(path):
        return [Refusal(label, _MERGE)]
    if _TRACKER_PATH.search(path) or _BOARD_PATH.search(path):
        return [Refusal(label, _API_ADVICE)]
    return []


def _graphql(label: str, words: List[str], opaque_input: bool) -> List[Refusal]:
    """A GraphQL write is refused when it mutates issues, labels, projects or merges.

    A query read from a file or stdin cannot be inspected, so it is refused too.
    """
    text = " ".join(words)
    if _MUTATION.search(text):
        return [Refusal(label, _API_ADVICE)] if _MUTATION_TARGET.search(text) else []
    opaque = opaque_input or any(word.startswith("@") or "=@" in word for word in words)
    return [Refusal(label, _API_ADVICE)] if opaque else []


def _http(words: List[str], flavor: str) -> List[Refusal]:
    method, body, url, query_only = _http_request(words[1:], flavor)
    if url is None:
        return []
    effective = method or ("POST" if body else "GET")
    if query_only or effective not in _WRITE_METHODS:
        return []
    label = f"{_basename(words[0])} {effective} {url.split('?', 1)[0]}"
    path = "/" + urlsplit(url if "://" in url else f"//{url}").path.strip("/")
    if _GRAPHQL_PATH.search(path):
        return _graphql(label, words[1:], False)
    return _path_refusal(label, path)


def _http_request(arguments: List[str], flavor: str) -> Tuple[Optional[str], bool, Optional[str], bool]:
    """(explicit method, sends a body, url, body is sent as the query string)."""
    method: Optional[str] = None
    body = False
    query_only = False
    url: Optional[str] = None
    value_flags = _CURL_VALUE_FLAGS if flavor == "curl" else frozenset()
    skip = False
    positional: List[str] = []
    for argument in arguments:
        if skip:
            skip = False
            continue
        name, equals, value = argument.partition("=")
        if argument.startswith("-"):
            if flavor == "curl":
                if name in ("-X", "--request"):
                    if equals:
                        method = value.upper()
                    else:
                        skip = True
                        method = _following(arguments, argument)
                elif argument.startswith("-X") and len(argument) > 2:
                    method = argument[2:].upper()
                elif name in _CURL_BODY_FLAGS:
                    body = True
                    skip = not equals
                elif argument[:2] in ("-d", "-F", "-T") and not argument.startswith("--"):
                    body = True
                elif name in ("-G", "--get"):
                    query_only = True
                elif name == "--url":
                    url = value if equals else _following(arguments, argument)
                    skip = not equals
                elif name in value_flags:
                    skip = not equals
            elif flavor == "wget":
                if name == "--method" and equals:
                    method = value.upper()
                elif name == "--method":
                    skip = True
                    method = _following(arguments, argument)
                elif name in _WGET_BODY_FLAGS:
                    body = True
                    skip = not equals
            continue
        positional.append(argument)
    if flavor == "httpie":
        if positional and _HTTP_METHOD.fullmatch(positional[0]):
            method = positional.pop(0)
        for argument in positional:
            if url is None and "=" not in argument and _HTTPIE_HOST.fullmatch(argument):
                url = argument
            elif "==" not in argument and ("=" in argument):
                body = True
        return method, body, url, False
    for argument in positional:
        if url is None and ("://" in argument or flavor == "curl" or flavor == "wget"):
            url = argument
    return method, body, url, query_only


def _following(arguments: List[str], flag: str) -> Optional[str]:
    position = arguments.index(flag)
    return arguments[position + 1].upper() if position + 1 < len(arguments) else None


def pushes(command: str) -> bool:
    """True when the command is a `git push` or `jj git push`."""
    for words in _simple_commands(command, 0):
        binary = _basename(words[0])
        rest = words[1:]
        if binary == "git":
            rest = _without_git_options(rest)
            if rest[:1] == ["push"]:
                return True
        elif binary == "jj" and [word for word in rest if not word.startswith("-")][:2] == ["git", "push"]:
            return True
    return False


def _without_git_options(arguments: List[str]) -> List[str]:
    rest = list(arguments)
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in ("-C", "-c", "--git-dir", "--work-tree") else rest[1:]
    return rest


def jj_push_revsets(command: str) -> List[str]:
    """The revset each `jj git push` in the command would send (mutable ancestors of what it names)."""
    found: List[str] = []
    for words in _simple_commands(command, 0):
        if _basename(words[0]) != "jj":
            continue
        rest = words[1:]
        if [word for word in rest if not word.startswith("-")][:2] != ["git", "push"]:
            continue
        revset = _push_revset(rest[rest.index("push") + 1:])
        if revset is not None:
            found.append(revset)
    return found


_REVSET_UNSAFE = re.compile(r'["\\\n]')


def _push_revset(arguments: List[str]) -> Optional[str]:
    """None when an argument cannot be put in a revset safely: the guard then stays silent."""
    selectors: List[str] = []
    position = 0
    while position < len(arguments):
        argument = arguments[position]
        flag, equals, value = argument.partition("=")
        if flag in ("--all", "--tracked"):
            selectors.append("bookmarks()")
        elif flag in ("--bookmark", "-b", "--revisions", "-r", "--change", "-c"):
            if not equals:
                position += 1
                value = arguments[position] if position < len(arguments) else ""
            if not value or _REVSET_UNSAFE.search(value):
                return None
            if flag in ("--bookmark", "-b"):
                selectors.append(f'bookmarks(exact:"{value}")')
            elif flag in ("--revisions", "-r"):
                selectors.append(f"({value})")
            else:
                return f"({value}) & mutable()"
        position += 1
    return "mutable() & ::(" + " | ".join(selectors or ["@"]) + ")"


def qew_pr_merges(command: str) -> List[int]:
    """Pull request numbers named by `qew pr merge N` simple commands (positive integers only)."""
    numbers: List[int] = []
    for words in _simple_commands(command, 0):
        if _basename(words[0]) != "qew" or words[1:3] != ["pr", "merge"]:
            continue
        arguments = words[3:]
        position = 0
        while position < len(arguments):
            argument = arguments[position]
            if argument == "--target":
                position += 2
                continue
            if not argument.startswith("-"):
                if argument.isascii() and argument.isdigit() and int(argument) > 0:
                    numbers.append(int(argument))
                break
            position += 1
    return numbers
