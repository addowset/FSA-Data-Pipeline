#!/usr/bin/env python
"""Matches FHRS INSERT events against Companies House companies.

For every INSERT event in diff_events not yet matched, looks up
candidates two ways: postcode-district + name similarity (the original
search), and a national name-only search independent of district (added
2026-08-28 -- catches a company registered via a formation agent or
personal address in a different district to where it actually trades,
confirmed real cases: "Soul Mama Islington", "Mamma Rosa London"). Both
channels' results are merged and the ranked evidence stored in
company_match_candidates / company_match_runs. See fsa_pipeline/matcher.py
for the approach and its trade-offs.

Does not decide NEW_VENUE / OPERATOR_CHANGE / UNKNOWN -- that's stage 5.

Reads only from the database, never touches raw files or the network.
Idempotent: an INSERT event already matched is skipped. Pass --force to
rematch everything (candidates can change as new Companies House data
arrives).

Usage:
    python scripts/match_companies_house.py [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.matcher import (
    build_address_density,
    build_companies_by_district,
    build_companies_by_first_word,
    build_word_idf,
    extract_operator_prefix,
    find_candidates,
    find_national_candidates,
    merge_candidates,
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(force: bool) -> int:
    config = load_config()
    logger = setup_logger("match_companies_house", config.log_dir / "match_companies_house.log")

    conn = db.connect(config.db_path)

    company_columns = ("company_number", "company_name", "address_line_1", "postal_code", "date_of_creation")
    companies = [
        dict(zip(company_columns, row))
        for row in conn.execute(
            "SELECT company_number, company_name, address_line_1, postal_code, date_of_creation FROM companies_current"
        ).fetchall()
    ]
    logger.info("%d companies loaded for candidate matching", len(companies))

    companies_by_district = build_companies_by_district(companies)
    companies_by_first_word = build_companies_by_first_word(companies)
    address_density = build_address_density(companies)
    idf = build_word_idf(companies)

    insert_events = conn.execute(
        "SELECT fhrsid, authority_code, collection_date FROM diff_events WHERE event_type = 'INSERT'"
    ).fetchall()
    logger.info("%d FHRS INSERT events to consider", len(insert_events))

    matched_count = 0
    already_count = 0
    found_candidate_count = 0

    for fhrsid, authority_code, collection_date in insert_events:
        if not force and db.already_matched(conn, fhrsid, collection_date):
            already_count += 1
            continue

        # COALESCE: prefer the bulk postcode, fall back to a live-API
        # backfill (fsa_pipeline/fhrs_live.py) when bulk has none. Never
        # the reverse -- bulk is authoritative when present.
        establishment = conn.execute(
            "SELECT business_name, COALESCE(post_code, postcode_from_live_api), address_line_1, first_seen_date "
            "FROM establishments_current WHERE fhrsid = ?", (fhrsid,)
        ).fetchone()

        if establishment is None:
            logger.warning("fhrsid %s has an INSERT event but no establishments_current row, skipping", fhrsid)
            continue

        business_name, post_code, address_line_1, first_seen_date = establishment
        district_candidates = find_candidates(
            business_name, post_code, companies_by_district, address_density,
            config.high_density_address_threshold, idf, address_line_1, first_seen_date, config.candidates_per_match,
        )
        national_candidates = find_national_candidates(
            business_name, companies_by_first_word, address_density,
            config.high_density_address_threshold, config.national_match_threshold, idf, config.candidates_per_match,
        )

        # "<operator> @ <site>" contract-catering naming (real cases:
        # "Aramark @ Drayton Manor High School") -- the site name dilutes
        # a full-string national match, so also search the operator name
        # alone when present. See extract_operator_prefix's docstring.
        operator_prefix = extract_operator_prefix(business_name)
        operator_candidates = (
            find_national_candidates(
                operator_prefix, companies_by_first_word, address_density,
                config.high_density_address_threshold, config.national_match_threshold, idf,
                config.candidates_per_match, strategy="operator-prefix",
            )
            if operator_prefix else []
        )

        candidates = merge_candidates(
            district_candidates, national_candidates + operator_candidates, config.candidates_per_match, first_seen_date,
        )

        db.record_match(conn, fhrsid, authority_code, collection_date, candidates, now_iso())

        matched_count += 1
        if candidates:
            found_candidate_count += 1
            top = candidates[0]
            logger.info(
                "fhrsid %s (%s): %d candidate(s), best=%s score=%.2f via %s%s",
                fhrsid, business_name, len(candidates), top.company_name, top.name_similarity_score,
                top.match_strategy, " [HIGH-DENSITY ADDRESS]" if top.is_high_density_address else "",
            )
        else:
            logger.info("fhrsid %s (%s): no candidates", fhrsid, business_name)

    logger.info(
        "done: %d matched (%d with candidates), %d already matched, %d total INSERT events",
        matched_count, found_candidate_count, already_count, len(insert_events),
    )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rematch INSERT events already matched")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(run(args.force))


if __name__ == "__main__":
    main()
