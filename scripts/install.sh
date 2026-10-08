#!/bin/sh
# Install qew for the current user on Linux or macOS: a virtual environment under
# ${XDG_DATA_HOME:-~/.local/share}/qew/venv and a `qew` link in ~/.local/bin.
# This front door only finds a Python 3.11+ (macOS ships an older one); setup_user.py does the work.
set -eu

here=$(cd "$(dirname "$0")" && pwd)

usable() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

for name in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$name" >/dev/null 2>&1 && usable "$name"; then
    exec "$name" "$here/setup_user.py" "$@"
  fi
done
for path in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$path" ] && usable "$path"; then
    exec "$path" "$here/setup_user.py" "$@"
  fi
done

echo "qew needs Python 3.11 or newer; install one (macOS: brew install python) and rerun." >&2
exit 3
