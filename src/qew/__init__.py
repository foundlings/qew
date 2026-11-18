"""Qew: an offline-first work ledger mirrored to forges."""

import os

__version__ = "7.0.4"

if os.environ.get("QEW_FILE_MAP"):  # a development aid for scripts/gate.py --changed: record which of these files a process ran
    from qew import filemap

    filemap.start(os.environ["QEW_FILE_MAP"])
