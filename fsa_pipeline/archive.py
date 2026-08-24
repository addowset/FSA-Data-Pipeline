"""Writes raw bytes to the dated archive, gzip-compressed, atomically, and
never overwrites an existing file.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path


class AlreadyArchived(Exception):
    """Raised when the target archive file already exists.

    The raw archive is append-only: existing files are never overwritten or
    deleted. Callers should treat this as "already done", not an error.
    """


def day_dir(raw_dir: Path, date_str: str) -> Path:
    path = raw_dir / date_str
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_gzip_atomic(content: bytes, dest: Path) -> None:
    """Gzip-compress content and write it to dest, or raise if dest exists.

    Writes to a temp file first and renames into place, so a crash mid-write
    can never leave a truncated file that looks complete.
    """
    if dest.exists():
        raise AlreadyArchived(str(dest))

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with gzip.open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
