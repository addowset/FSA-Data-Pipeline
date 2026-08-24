#!/usr/bin/env python
"""Parses a day's raw Companies House archive into the local database.

Reads raw/companies-house/<date>/ (written by collect_companies_house.py),
which must already exist -- this script never fetches anything over the
network. Reads every page_NNNN.json.gz for the date, parses each item,
and upserts companies_current / appends to company_observations (see
fsa_pipeline/db.py's dedup-on-write design, same pattern as FHRS).

Idempotent by default: a date already successfully parsed is skipped.
Pass --force to reparse anyway.

Usage:
    python scripts/parse_companies_house.py [--date YYYY-MM-DD] [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.companies_house import RecordParseError, hits_from_page_bytes, items_from_page_bytes, parse_company_item
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(date_str: str, force: bool) -> int:
    config = load_config()
    logger = setup_logger("companies_house_parse", config.log_dir / f"companies_house_parse_{date_str}.log")

    conn = db.connect(config.db_path)

    if not force and db.already_parsed_companies_house(conn, date_str):
        logger.info("%s already parsed, nothing to do (use --force to reparse)", date_str)
        return 0

    day_path = config.ch_raw_dir / date_str
    page_paths = sorted(day_path.glob("page_*.json.gz"))

    if not page_paths:
        logger.error("no raw pages found for %s at %s -- run collect_companies_house.py first", date_str, day_path)
        return 1

    companies: list[dict] = []
    skipped_records = 0
    hits = None

    for i, page_path in enumerate(page_paths):
        try:
            with gzip.open(page_path, "rb") as f:
                page_bytes = f.read()
        except Exception as e:
            logger.error("could not read %s: %s", page_path, e)
            db.record_companies_house_run(
                conn, collection_date=date_str, hits=None, pages_parsed=i,
                status="parse_error", error_message=str(e), parsed_at=now_iso(),
            )
            return 1

        if hits is None:
            hits = hits_from_page_bytes(page_bytes)

        for item in items_from_page_bytes(page_bytes):
            try:
                companies.append(parse_company_item(item))
            except RecordParseError as e:
                skipped_records += 1
                logger.warning("skipped record in %s: %s", page_path.name, e)

    stats = db.ingest_companies(conn, date_str, companies)
    db.record_companies_house_run(
        conn, collection_date=date_str, hits=hits, pages_parsed=len(page_paths),
        status="ok", skipped_records=skipped_records, parsed_at=now_iso(),
    )

    logger.info(
        "done: %d pages, %d companies parsed (%d first-seen, %d changed, %d unchanged), %d skipped records",
        len(page_paths), len(companies), stats.first_seen, stats.changed, stats.unchanged, skipped_records,
    )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to parse, YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true", help="Reparse even if already parsed for this date")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str, args.force))


if __name__ == "__main__":
    main()
