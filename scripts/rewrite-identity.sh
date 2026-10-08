#!/bin/sh
# Take one identity out of git (or colocated jj) repositories and put another in: authors, committers,
# commit messages and every file revision across all refs, using git-filter-repo.
#
#   rewrite-identity.sh [--dry-run] [--push] OLD NEW REPO...
#
# --dry-run  report how many commits mention OLD; change nothing
# --push     force-push every branch and tag to `origin` afterwards (history is rewritten: collaborators re-clone)
# A colocated jj repo is re-initialised afterwards. Refuses a dirty tree, a jj secondary workspace and unsafe names.
set -eu

dry=0
push=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --push) push=1; shift ;;
    --) shift; break ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
if [ $# -lt 3 ]; then
  echo "usage: rewrite-identity.sh [--dry-run] [--push] OLD NEW REPO..." >&2
  exit 2
fi
old=$1
new=$2
shift 2

safe() { printf '%s' "$1" | grep -Eq '^[A-Za-z0-9._-]+$'; }
if ! safe "$old" || ! safe "$new"; then
  echo "OLD and NEW must be letters, digits, dot, dash or underscore" >&2
  exit 2
fi
if [ "$dry" -eq 0 ] && ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "git-filter-repo is required (pip install git-filter-repo)" >&2
  exit 3
fi

count() {
  git log --all -S"$old" --format=%H | wc -l | tr -d ' '
}

status=0
for repo in "$@"; do
  if ! git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "$repo: not a git repository" >&2
    status=2
    continue
  fi
  cd "$repo"
  if [ -f .jj/repo ]; then
    echo "$repo: a jj secondary workspace; run this in the main repository after forgetting the workspaces" >&2
    status=2
    cd - >/dev/null
    continue
  fi
  if [ -n "$(git status --porcelain)" ]; then
    echo "$repo: working tree is not clean; commit or stash first" >&2
    status=2
    cd - >/dev/null
    continue
  fi
  touching=$(count)
  names=$(git log --all --format='%an%n%ae%n%cn%n%ce' | grep -c -F "$old" || true)
  echo "$repo: commits touching the text: $touching; author/committer fields naming it: $names"
  if [ "$dry" -eq 1 ]; then
    cd - >/dev/null
    continue
  fi
  url=$(git remote get-url origin 2>/dev/null || true)
  expressions=$(mktemp)
  printf 'literal:%s==>%s\n' "$old" "$new" > "$expressions"
  git filter-repo --force --replace-text "$expressions" --replace-message "$expressions" \
    --name-callback "return name.replace(b'$old', b'$new')" \
    --email-callback "return email.replace(b'$old', b'$new')" >/dev/null 2>&1
  rm -f "$expressions"
  if [ -n "$url" ]; then
    git remote add origin "$url" 2>/dev/null || git remote set-url origin "$url"
  fi
  if [ -d .jj ]; then
    rm -rf .jj
    if command -v jj >/dev/null 2>&1; then
      jj git init --colocate >/dev/null 2>&1
      [ -n "$url" ] && jj bookmark track main --remote origin >/dev/null 2>&1 || true
    fi
  fi
  left=$(count)
  if [ "$left" != "0" ]; then
    echo "$repo: $left commits still mention '$old'" >&2
    status=1
  else
    echo "$repo: rewritten, no commit mentions '$old'"
  fi
  if [ "$push" -eq 1 ] && [ -n "$url" ]; then
    git push --force --all origin
    git push --force --tags origin
  fi
  cd - >/dev/null
done
exit "$status"
