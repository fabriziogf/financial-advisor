"""Where local data lives — deliberately NOT inside the repository.

The working tree is a public git repo that also happens to sit inside an
iCloud-synced directory. Either alone is a reason to keep financial data out of it;
together they make it the single most likely way this project leaks. A database
written next to the source would be uploaded to Apple's servers on first import
and synced to every device on the account, with nothing visibly wrong.

So data lives under the XDG data directory instead. This is a structural control,
not a policy: a file that is not in the working tree cannot be committed by a
mistaken `git add -A`, and does not depend on `.gitignore` staying correct. It also
survives `rm -rf` of the checkout and a fresh clone.

Override with FA_DATA_DIR (used by the test suite to redirect to a tmpdir).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

__all__ = ["data_dir", "database_path", "ensure_data_dir", "OWNER_ONLY"]

OWNER_ONLY = 0o700
DB_FILENAME = "advisor.db"


def data_dir() -> Path:
    """Resolve the data directory without creating it."""
    override = os.environ.get("FA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return (root / "financial-advisor").resolve()


def ensure_data_dir() -> Path:
    """Create the data directory if needed, owner-only.

    0700 is part of how P4 is satisfied for M0: FileVault encrypts the disk at
    rest, and these permissions keep other local accounts out. Neither protects
    against a process running as this user — that is the gap SQLCipher would close,
    and the reason all DB access is funnelled through db/connection.py so adopting
    it later stays a one-file change.
    """
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    current = stat.S_IMODE(path.stat().st_mode)
    if current != OWNER_ONLY:
        path.chmod(OWNER_ONLY)
    return path


def database_path() -> Path:
    override = os.environ.get("FA_DATABASE")
    if override:
        return Path(override).expanduser().resolve()
    return data_dir() / DB_FILENAME
