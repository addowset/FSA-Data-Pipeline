#!/usr/bin/env python
"""Parses a day's raw FHRS archive into the local database.

Reads raw/fhrs/<date>/ (written by collect_fhrs_bulk.py), which must
already exist -- this script never fetches anything over the network.
For each authority: records the collection run (including ExtractDate,
the freshness-monitor field), and upserts establishments_current /
appends to observations (see fsa_pipeline/db.py for the storage design).

Idempotent by default: an authority already successfully parsed for the
given date is skipped. Pass --force to reparse anyway (safe -- the
database is derived state, unlike the raw archive, so reparsing just
recomputes the same fingerprints and current-state rows).

Usage:
    python scripts/parse_fhrs_bulk.py [--date YYYY-MM-DD] [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.config import load_config
from fsa_pipeline.fhrs_bulk import parse_authorities
from fsa_pipeline.fhrs_parse import parse_bulk_file
from fsa_pipeline.logging_utils import setup_logger

AUTHORITIES_INDEX_FILENAME = "_authorities-index.xml.gz"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(date_str: str, force: bool) -> int:
    config = load_config()
    logger = setup_logger("fhrs_parse", config.log_dir / f"fhrs_parse_{date_str}.log")

    day_path = config.raw_dir / date_str
    index_path = day_path / AUTHORITIES_INDEX_FILENAME

    if not index_path.exists():
        logger.error("no authorities index found for %s at %s -- run collect_fhrs_bulk.py first", date_str, index_path)
        return 1

    with gzip.open(index_path, "rb") as f:
        authorities = parse_authorities(f.read())

    conn = db.connect(config.db_path)
    db.upsert_authorities(conn, authorities, date_str)
    logger.info("%d authorities to parse for %s", len(authorities), date_str)

    parsed_count = 0
    already_parsed_count = 0
    failed: list[dict] = []
    total_first_seen = total_changed = total_unchanged = total_skipped_records = 0

    for authority in authorities:
        raw_path = day_path / authority.archive_filename

        if not raw_path.exists():
            failed.append({"code": authority.code, "error": "raw archive file missing"})
            logger.warning("%s: raw archive file missing at %s, skipping", authority.code, raw_path)
            continue

        if not force and db.already_parsed(conn, authority.code, date_str):
            already_parsed_count += 1
            continue

        try:
            with gzip.open(raw_path, "rb") as f:
                xml_bytes = f.read()
            parsed = parse_bulk_file(xml_bytes)
        except Exception as e:
            failed.append({"code": authority.code, "error": str(e)})
            db.record_collection_run(
                conn,
                authority_code=authority.code,
                collection_date=date_str,
                extract_date=None,
                item_count=None,
                return_code=None,
                raw_file_path=str(raw_path.relative_to(config.raw_dir.parent)),
                status="parse_error",
                error_message=str(e),
                parsed_at=now_iso(),
            )
            logger.error("%s: parse FAILED: %s", authority.code, e)
            continue

        stats = db.ingest_establishments(conn, authority.code, date_str, parsed.establishments)
        db.record_collection_run(
            conn,
            authority_code=authority.code,
            collection_date=date_str,
            extract_date=parsed.header.extract_date,
            item_count=parsed.header.item_count,
            return_code=parsed.header.return_code,
            raw_file_path=str(raw_path.relative_to(config.raw_dir.parent)),
            status="ok",
            skipped_records=parsed.skipped_records,
            parsed_at=now_iso(),
        )

        parsed_count += 1
        total_first_seen += stats.first_seen
        total_changed += stats.changed
        total_unchanged += stats.unchanged
        total_skipped_records += parsed.skipped_records

        logger.info(
            "[%d/%d] %s (%s): %d first-seen, %d changed, %d unchanged, %d skipped records, extract_date=%s",
            parsed_count + already_parsed_count, len(authorities), authority.code, authority.name,
            stats.first_seen, stats.changed, stats.unchanged, parsed.skipped_records, parsed.header.extract_date,
        )

    logger.info(
        "done: %d parsed, %d already parsed, %d failed (of %d total); "
        "%d first-seen, %d changed, %d unchanged, %d records skipped",
        parsed_count, already_parsed_count, len(failed), len(authorities),
        total_first_seen, total_changed, total_unchanged, total_skipped_records,
    )

    if failed:
        logger.warning("run incomplete: re-run the same command to retry only the failed authorities")
        return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to parse, YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true", help="Reparse authorities already parsed for this date")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str, args.force))


if __name__ == "__main__":
    main()
