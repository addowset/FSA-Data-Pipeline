#!/usr/bin/env python
"""Wipes the derived database and rebuilds it from the raw archive.

The database is derived, rebuildable state -- the raw archive under raw/
is the actual asset (see CLAUDE.md). This script exists for exactly the
scenario the project brief calls out: "Parsers will improve; I want to
reprocess history when they do." Also the right tool for recovering from
a bug in the parsing/fingerprint logic that corrupted derived state
without touching the raw archive.

Replays every dated directory under raw/fhrs/ through parse_fhrs_bulk.py,
then diff_fhrs.py, in date order; every dated directory under
raw/companies-house/ through parse_companies_house.py; then
match_companies_house.py once at the end (matching needs the fully
rebuilt establishments_current, diff_events, and companies_current, so it
can't be interleaved with the per-date replay loops). All against a
freshly emptied database. Never touches the network -- everything here
is a replay of what's already archived under raw/.

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


def replay_dates(python: str, scripts_dir: Path, project_root: Path, script_name: str, dates: list[str]) -> None:
    for date_str in dates:
        print(f"--- {script_name} {date_str} ---")
        result = subprocess.run(
            [python, str(scripts_dir / script_name), "--date", date_str],
            cwd=str(project_root),
        )
        if result.returncode != 0:
            print(f"{script_name} failed for {date_str}, aborting rebuild")
            sys.exit(1)


def main() -> None:
    config = load_config()

    fhrs_dates = sorted(p.name for p in config.raw_dir.glob("*") if p.is_dir())
    ch_dates = sorted(p.name for p in config.ch_raw_dir.glob("*") if p.is_dir()) if config.ch_raw_dir.exists() else []

    if not fhrs_dates:
        print("no FHRS raw archive directories found, nothing to rebuild from")
        sys.exit(1)

    print(f"wiping {config.db_path}")
    conn = db.connect(config.db_path)
    conn.executescript(
        "DROP TABLE IF EXISTS company_match_candidates;"
        "DROP TABLE IF EXISTS company_match_runs;"
        "DROP TABLE IF EXISTS diff_events;"
        "DROP TABLE IF EXISTS diff_runs;"
        "DROP TABLE IF EXISTS observations;"
        "DROP TABLE IF EXISTS establishments_current;"
        "DROP TABLE IF EXISTS collection_runs;"
        "DROP TABLE IF EXISTS authorities;"
        "DROP TABLE IF EXISTS company_observations;"
        "DROP TABLE IF EXISTS companies_current;"
        "DROP TABLE IF EXISTS companies_house_runs;"
    )
    conn.executescript(db.SCHEMA)
    conn.commit()
    conn.close()

    python = sys.executable
    scripts_dir = Path(__file__).parent
    project_root = scripts_dir.parent

    print(f"replaying {len(fhrs_dates)} FHRS day(s): {', '.join(fhrs_dates)}")
    replay_dates(python, scripts_dir, project_root, "parse_fhrs_bulk.py", fhrs_dates)

    # Diffing must run in date order too, and only after every day is
    # parsed -- the bulk-reupload guard looks at each authority's trailing
    # history of prior diff_runs, so this has to be a second full pass,
    # not interleaved with the parse loop above.
    replay_dates(python, scripts_dir, project_root, "diff_fhrs.py", fhrs_dates)

    if ch_dates:
        print(f"replaying {len(ch_dates)} Companies House day(s): {', '.join(ch_dates)}")
        replay_dates(python, scripts_dir, project_root, "parse_companies_house.py", ch_dates)

        print("--- matching ---")
        result = subprocess.run([python, str(scripts_dir / "match_companies_house.py")], cwd=str(project_root))
        if result.returncode != 0:
            print("matching failed, aborting rebuild")
            sys.exit(1)
    else:
        print("no Companies House raw archive found, skipping companies/matching replay")

    print("rebuild complete")


if __name__ == "__main__":
    main()
