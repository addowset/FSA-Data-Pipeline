#!/usr/bin/env python
"""Classifies each FHRS INSERT event as NEW_VENUE / OWNERSHIP_CHANGE / UNKNOWN.

Reads only from the database, never touches raw files or the network.
Must run after diff_fhrs.py and match_companies_house.py for the same
data. See fsa_pipeline/classifier.py for the classification logic and
its reasoning, including the "Stage 5 design commitment" (address-history
lookup queries establishments_current, never observations).

Idempotent per (FHRSID, INSERT date). Pass --force to reclassify
everything (evidence can change as new Companies House data or new
FHRS establishments arrive).

Usage:
    python scripts/classify_insertions.py [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.classifier import build_address_index, classify, find_predecessor
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.matcher import Candidate


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_establishments(conn) -> list[dict]:
    columns = ("fhrsid", "business_name", "address_line_1", "postcode", "first_seen_date", "last_seen_date")
    rows = conn.execute(
        "SELECT fhrsid, business_name, address_line_1, COALESCE(post_code, postcode_from_live_api), "
        "first_seen_date, last_seen_date FROM establishments_current"
    ).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def load_best_candidates(conn) -> dict[tuple[int, str], Candidate]:
    rows = conn.execute(
        """
        SELECT fhrsid, insert_collection_date, company_number, company_name, name_similarity_score,
               postcode_district, address_company_count, is_high_density_address
        FROM company_match_candidates WHERE rank = 1
        """
    ).fetchall()
    result = {}
    for fhrsid, insert_date, number, name, score, district, count, high_density in rows:
        result[(fhrsid, insert_date)] = Candidate(
            company_number=number, company_name=name, name_similarity_score=score,
            postcode_district=district, address_company_count=count,
            is_high_density_address=bool(high_density),
        )
    return result


def run(force: bool) -> int:
    config = load_config()
    logger = setup_logger("classify_insertions", config.log_dir / "classify_insertions.log")

    conn = db.connect(config.db_path)

    logger.info("loading establishments_current for address-history index")
    establishments = load_establishments(conn)
    establishments_by_id = {e["fhrsid"]: e for e in establishments}
    address_index = build_address_index(establishments)
    logger.info("%d establishments loaded, %d distinct addresses", len(establishments), len(address_index))

    best_candidates = load_best_candidates(conn)
    match_counts = dict(
        conn.execute("SELECT fhrsid || '|' || insert_collection_date, candidates_found FROM company_match_runs").fetchall()
    )

    insert_events = conn.execute(
        "SELECT fhrsid, authority_code, collection_date FROM diff_events WHERE event_type = 'INSERT'"
    ).fetchall()
    logger.info("%d FHRS INSERT events to consider", len(insert_events))

    classified = 0
    already_count = 0
    counts = Counter()

    for fhrsid, authority_code, collection_date in insert_events:
        if not force and db.already_classified(conn, fhrsid, collection_date):
            already_count += 1
            continue

        establishment = establishments_by_id.get(fhrsid)
        if establishment is None:
            logger.warning("fhrsid %s has an INSERT event but no establishments_current row, skipping", fhrsid)
            continue

        predecessor = find_predecessor(
            fhrsid, establishment["address_line_1"], establishment["postcode"],
            establishment["first_seen_date"], address_index,
        )
        best_candidate = best_candidates.get((fhrsid, collection_date))
        candidates_found = match_counts.get(f"{fhrsid}|{collection_date}", 0)

        result = classify(
            postcode=establishment["postcode"],
            candidates_found=candidates_found,
            best_candidate=best_candidate,
            predecessor=predecessor,
            config=config,
        )

        db.record_classification(conn, fhrsid, authority_code, collection_date, result, now_iso())

        classified += 1
        counts[(result.classification, result.confidence)] += 1

    logger.info("done: %d classified, %d already classified, %d total INSERT events", classified, already_count, len(insert_events))
    for (classification, confidence), n in sorted(counts.items()):
        logger.info("  %s / %s: %d", classification, confidence, n)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Reclassify INSERT events already classified")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(run(args.force))


if __name__ == "__main__":
    main()
