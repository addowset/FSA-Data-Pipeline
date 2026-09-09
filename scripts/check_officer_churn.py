#!/usr/bin/env python
"""Computes an officer-churn signal for OPERATOR_CHANGE classifications.

Opt-in: exits immediately unless config.toml's [officer_churn].enabled
is true (disabled by default). For every OPERATOR_CHANGE classification
with a Companies House company cited as evidence -- never the whole
company pool -- fetches that company's current officer list and reduces
it to three numbers (officer_count, appointed_near_event,
resigned_near_event; see fsa_pipeline/officer_churn.py) without ever
storing, logging, or otherwise persisting any per-officer field (name,
date of birth, nationality, address).

See config.toml's [officer_churn] comment and README "Design notes" for
the privacy reasoning behind these boundaries before enabling this.

Idempotent: an (company_number, fhrsid) pair already checked is skipped.
Pass --force to recheck everything (an officer could have been appointed
or resigned since the last check).

Usage:
    python scripts/check_officer_churn.py [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.companies_house import MissingApiKey, build_session, fetch_officers, get_api_key
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.officer_churn import compute_officer_churn_signal


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(force: bool) -> int:
    config = load_config()
    logger = setup_logger("check_officer_churn", config.log_dir / "check_officer_churn.log")

    if not config.officer_churn_enabled:
        logger.info(
            "officer churn checking is disabled (config.toml [officer_churn].enabled = false), nothing to do"
        )
        return 0

    try:
        api_key = get_api_key()
    except MissingApiKey as e:
        logger.error(str(e))
        return 1

    conn = db.connect(config.db_path)
    session = build_session(api_key, config)

    if force:
        targets = conn.execute(
            """
            SELECT c.fhrsid, c.evidence_company_number, e.first_seen_date
            FROM classifications c
            JOIN establishments_current e ON e.fhrsid = c.fhrsid
            WHERE c.classification = 'OPERATOR_CHANGE' AND c.evidence_company_number IS NOT NULL
            """
        ).fetchall()
    else:
        targets = db.get_operator_changes_needing_officer_check(conn)

    logger.info("%d OPERATOR_CHANGE event(s) to check for officer churn", len(targets))

    checked = 0
    errors = 0

    for fhrsid, company_number, first_seen_date in targets:
        try:
            # officers is intentionally never logged, stored, or referenced
            # again after this line -- see fetch_officers's docstring.
            officers = fetch_officers(session, config, company_number)
            signal = compute_officer_churn_signal(officers, first_seen_date, config.officer_churn_window_days)
            db.record_officer_churn_check(conn, fhrsid, company_number, signal, config.officer_churn_window_days, now_iso())
            checked += 1
            logger.info(
                "fhrsid %s company %s: %d officer(s), appointed_near_event=%s resigned_near_event=%s",
                fhrsid, company_number, signal["officer_count"],
                signal["appointed_near_event"], signal["resigned_near_event"],
            )
        except Exception as e:
            errors += 1
            logger.warning("failed to check officer churn for fhrsid %s company %s: %s", fhrsid, company_number, e)

        time.sleep(config.officer_churn_request_delay_seconds)

    logger.info("done: %d checked, %d errors, %d total target(s)", checked, errors, len(targets))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Recheck OPERATOR_CHANGE events already checked")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(run(args.force))


if __name__ == "__main__":
    main()
