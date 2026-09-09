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
lookup_operator_companies.py, match_companies_house.py,
backfill_postcodes.py, and classify_insertions.py once each at the end,
in that order (each depends on the fully rebuilt state from the step
before, so none of these can be interleaved with the per-date replay
loops above). All against a freshly emptied database. Mostly a replay of
what's already archived under raw/, not a re-fetch -- postcode backfill
reuses raw/fhrs-live/<date>/ if present, same as a normal run. The
exception is lookup_operator_companies.py: its raw responses ARE
archived (raw/companies-house/operator-search/<date>/), but there's no
replay-from-archive path built for them yet (unlike parse_fhrs_bulk.py/
parse_companies_house.py), so a rebuild re-runs it live and WILL hit the
Companies House API again for every operator prefix not already
satisfied by the just-replayed bulk pool -- expect a live network call
during a rebuild, both for this step and (if [officer_churn] is enabled)
check_officer_churn.py.

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
        "DROP TABLE IF EXISTS officer_churn_checks;"
        "DROP TABLE IF EXISTS operator_search_checks;"
        "DROP TABLE IF EXISTS classifications;"
        "DROP TABLE IF EXISTS company_match_candidates;"
        "DROP TABLE IF EXISTS company_match_runs;"
        "DROP TABLE IF EXISTS postcode_backfill_events;"
        "DROP TABLE IF EXISTS postcode_backfill_runs;"
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

    def run_once(script_name: str) -> None:
        print(f"--- {script_name} ---")
        result = subprocess.run([python, str(scripts_dir / script_name)], cwd=str(project_root))
        if result.returncode != 0:
            print(f"{script_name} failed, aborting rebuild")
            sys.exit(1)

    run_once("backfill_postcodes.py")

    if ch_dates:
        print(f"replaying {len(ch_dates)} Companies House day(s): {', '.join(ch_dates)}")
        replay_dates(python, scripts_dir, project_root, "parse_companies_house.py", ch_dates)
        run_once("lookup_operator_companies.py")
        run_once("match_companies_house.py")
    else:
        print("no Companies House raw archive found, skipping companies/matching replay")

    run_once("classify_insertions.py")

    # Opt-in and disabled by default (config.toml's [officer_churn]) --
    # a no-op unless the user has explicitly enabled it, same as every
    # other step here being skippable when its raw archive is absent.
    run_once("check_officer_churn.py")

    print("rebuild complete")


if __name__ == "__main__":
    main()
