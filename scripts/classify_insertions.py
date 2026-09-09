#!/usr/bin/env python
"""Classifies each FHRS INSERT event as NEW_VENUE / OPERATOR_CHANGE / UNKNOWN.

Reads only from the database, never touches raw files or the network.
Must run after diff_fhrs.py and match_companies_house.py for the same
data. See fsa_pipeline/classifier.py for the classification logic and
its reasoning, including the "Stage 5 design commitment" (address-history
lookup queries establishments_current, never observations).

Idempotent per (FHRSID, INSERT date). Pass --force to reclassify
everything (evidence can change as new Companies House data or new
FHRS establishments arrive).

Also writes a short review-queue log (_review_queue_<date>.log next to
the main log) listing NEW_VENUE/MEDIUM classifications from this run --
borderline matches worth a manual look, per the user's request
2026-08-28. Deliberately narrow (one confidence tier, one classification)
so it stays short enough to actually check.

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
from fsa_pipeline.classifier import (
    build_address_index,
    build_company_operator_index,
    build_postcode_index,
    classify,
    count_operator_venues,
    find_existing_operator,
    find_predecessor,
)
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.matcher import Candidate, build_word_idf


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
               postcode_district, address_company_count, is_high_density_address, match_strategy, date_of_creation,
               address_matches_establishment
        FROM company_match_candidates WHERE rank = 1
        """
    ).fetchall()
    result = {}
    for fhrsid, insert_date, number, name, score, district, count, high_density, strategy, created, address_match in rows:
        result[(fhrsid, insert_date)] = Candidate(
            company_number=number, company_name=name, name_similarity_score=score,
            postcode_district=district, address_company_count=count,
            is_high_density_address=bool(high_density), match_strategy=strategy, date_of_creation=created,
            address_matches_establishment=bool(address_match),
        )
    return result


def load_matched_venues(conn, medium_threshold: float) -> list[dict]:
    """Every FHRSID with a confident (>= medium threshold) company match,
    for the existing-operator check -- deliberately not limited to
    currently-being-classified INSERT events, since the "other venue"
    might have been matched on an earlier run."""
    rows = conn.execute(
        """
        SELECT r.fhrsid, r.best_match_company_number, e.business_name, e.first_seen_date
        FROM company_match_runs r
        JOIN establishments_current e ON e.fhrsid = r.fhrsid
        WHERE r.best_match_score >= ? AND r.best_match_company_number IS NOT NULL
        """,
        (medium_threshold,),
    ).fetchall()
    columns = ("fhrsid", "company_number", "business_name", "first_seen_date")
    return [dict(zip(columns, row)) for row in rows]


def run(force: bool) -> int:
    config = load_config()
    logger = setup_logger("classify_insertions", config.log_dir / "classify_insertions.log")

    conn = db.connect(config.db_path)

    logger.info("loading establishments_current for address-history index")
    establishments = load_establishments(conn)
    establishments_by_id = {e["fhrsid"]: e for e in establishments}
    address_index = build_address_index(establishments)
    postcode_index = build_postcode_index(establishments)
    logger.info("%d establishments loaded, %d distinct exact addresses", len(establishments), len(address_index))

    # Same word-rarity model the matcher uses (fsa_pipeline/matcher.py's
    # build_word_idf, see its docstring) -- reused here so the
    # address-history fallback's FHRS-name-vs-FHRS-name comparison scores
    # a shared distinctive word the same way a shared generic descriptor
    # ("FISH BAR", "TAKEAWAY") is discounted in the company matcher.
    companies = [
        {"company_name": row[0]}
        for row in conn.execute("SELECT company_name FROM companies_current").fetchall()
    ]
    idf = build_word_idf(companies)

    best_candidates = load_best_candidates(conn)
    matched_venues = load_matched_venues(conn, config.new_venue_medium_threshold)
    operator_index = build_company_operator_index(matched_venues)
    logger.info("%d confidently-matched venues loaded for existing-operator check", len(matched_venues))

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
    review_queue = []

    for fhrsid, authority_code, collection_date in insert_events:
        if not force and db.already_classified(conn, fhrsid, collection_date):
            already_count += 1
            continue

        establishment = establishments_by_id.get(fhrsid)
        if establishment is None:
            logger.warning("fhrsid %s has an INSERT event but no establishments_current row, skipping", fhrsid)
            continue

        first_seen_date = establishment["first_seen_date"]

        predecessor = find_predecessor(
            fhrsid, establishment["business_name"], establishment["address_line_1"], establishment["postcode"],
            first_seen_date, address_index, postcode_index, idf, config.address_history_fallback_threshold,
        )
        best_candidate = best_candidates.get((fhrsid, collection_date))
        candidates_found = match_counts.get(f"{fhrsid}|{collection_date}", 0)

        existing_operator = None
        operator_venue_count = 0
        if best_candidate is not None:
            existing_operator = find_existing_operator(fhrsid, best_candidate.company_number, first_seen_date, operator_index)
            operator_venue_count = count_operator_venues(best_candidate.company_number, operator_index)

        result = classify(
            first_seen_date=first_seen_date,
            postcode=establishment["postcode"],
            candidates_found=candidates_found,
            best_candidate=best_candidate,
            predecessor=predecessor,
            existing_operator=existing_operator,
            config=config,
            operator_venue_count=operator_venue_count,
        )

        db.record_classification(conn, fhrsid, authority_code, collection_date, result, now_iso())

        classified += 1
        counts[(result.classification, result.confidence)] += 1

        if result.classification == "NEW_VENUE" and result.confidence == "MEDIUM":
            review_queue.append((fhrsid, establishment["business_name"], result.reason))

    logger.info("done: %d classified, %d already classified, %d total INSERT events", classified, already_count, len(insert_events))
    for (classification, confidence), n in sorted(counts.items()):
        logger.info("  %s / %s: %d", classification, confidence, n)

    if review_queue:
        review_path = config.log_dir / f"_review_queue_{dt.date.today().isoformat()}.log"
        with open(review_path, "a", encoding="utf-8") as f:
            f.write(f"--- run at {now_iso()} ---\n")
            for fhrsid, name, reason in review_queue:
                f.write(f"FHRSID {fhrsid} ({name}): {reason}\n")
        logger.info("%d entries appended to review queue: %s", len(review_queue), review_path)

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
