#!/usr/bin/env python
"""Classifies each authority's changes for a day as INSERT/UPDATE/DELETE.

Reads only from the database (observations, establishments_current,
collection_runs) -- never touches raw files or the network. Must run
after parse_fhrs_bulk.py for the same date.

An authority's first-ever successful collection has no prior snapshot to
diff against, so no events are emitted for it (see fsa_pipeline/diff_engine.py).

Idempotent by default: an authority already diffed for the given date is
skipped. Pass --force to recompute (safe -- diff_events/diff_runs are
derived from establishments_current/observations, not raw truth).

Usage:
    python scripts/diff_fhrs.py [--date YYYY-MM-DD] [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.config import load_config
from fsa_pipeline.diff_engine import already_diffed, compute_diff_for_authority, record_diff
from fsa_pipeline.logging_utils import setup_logger


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(date_str: str, force: bool) -> int:
    config = load_config()
    logger = setup_logger("fhrs_diff", config.log_dir / f"fhrs_diff_{date_str}.log")

    conn = db.connect(config.db_path)

    authority_codes = [
        r[0]
        for r in conn.execute(
            "SELECT authority_code FROM collection_runs WHERE collection_date = ? AND status = 'ok' ORDER BY authority_code",
            (date_str,),
        ).fetchall()
    ]

    if not authority_codes:
        logger.error("no successfully-parsed authorities found for %s -- run parse_fhrs_bulk.py first", date_str)
        return 1

    logger.info("%d authorities to diff for %s", len(authority_codes), date_str)

    diffed_count = 0
    already_count = 0
    bootstrap_count = 0
    quarantined_count = 0
    total_insert = total_update = total_delete = 0

    for i, authority_code in enumerate(authority_codes, start=1):
        if not force and already_diffed(conn, authority_code, date_str):
            already_count += 1
            continue

        result = compute_diff_for_authority(conn, authority_code, date_str, config)
        record_diff(conn, authority_code, date_str, result, now_iso())

        if result.previous_collection_date is None:
            bootstrap_count += 1
            logger.info("[%d/%d] %s: bootstrap day, no prior snapshot -- no events emitted",
                        i, len(authority_codes), authority_code)
            continue

        diffed_count += 1
        total_insert += result.insert_count
        total_update += result.update_count
        total_delete += result.delete_count

        flag = ""
        if result.quarantined:
            quarantined_count += 1
            flag = f" [QUARANTINED: {result.quarantine_reason}]"

        logger.info(
            "[%d/%d] %s: %d insert, %d update, %d delete (vs %s)%s",
            i, len(authority_codes), authority_code,
            result.insert_count, result.update_count, result.delete_count,
            result.previous_collection_date, flag,
        )

    logger.info(
        "done: %d diffed, %d bootstrap (skipped), %d already diffed, %d quarantined; "
        "%d insert, %d update, %d delete",
        diffed_count, bootstrap_count, already_count, quarantined_count,
        total_insert, total_update, total_delete,
    )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to diff, YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true", help="Recompute authorities already diffed for this date")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str, args.force))


if __name__ == "__main__":
    main()
