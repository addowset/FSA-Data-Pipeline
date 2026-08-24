#!/usr/bin/env python
"""Wipes the derived database and rebuilds it from the raw archive.

The database is derived, rebuildable state -- the raw archive under raw/
is the actual asset (see CLAUDE.md). This script exists for exactly the
scenario the project brief calls out: "Parsers will improve; I want to
reprocess history when they do." Also the right tool for recovering from
a bug in the parsing/fingerprint logic that corrupted derived state
without touching the raw archive.

Replays every dated directory under raw/fhrs/ through parse_fhrs_bulk.py,
in date order, against a freshly emptied database.

Usage:
    python scripts/rebuild_db.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.config import load_config


def main() -> None:
    config = load_config()

    dates = sorted(p.name for p in config.raw_dir.glob("*") if p.is_dir())
    if not dates:
        print("no raw archive directories found, nothing to rebuild from")
        sys.exit(1)

    print(f"wiping {config.db_path}")
    conn = db.connect(config.db_path)
    conn.executescript(
        "DROP TABLE IF EXISTS observations;"
        "DROP TABLE IF EXISTS establishments_current;"
        "DROP TABLE IF EXISTS collection_runs;"
        "DROP TABLE IF EXISTS authorities;"
    )
    conn.executescript(db.SCHEMA)
    conn.commit()
    conn.close()

    print(f"replaying {len(dates)} day(s): {', '.join(dates)}")
    python = sys.executable
    for date_str in dates:
        print(f"--- {date_str} ---")
        result = subprocess.run(
            [python, str(Path(__file__).parent / "parse_fhrs_bulk.py"), "--date", date_str],
            cwd=str(Path(__file__).parent.parent),
        )
        if result.returncode != 0:
            print(f"parse failed for {date_str}, aborting rebuild")
            sys.exit(1)

    print("rebuild complete")


if __name__ == "__main__":
    main()
