#!/usr/bin/env python
"""Recovers postcodes missing from bulk FHRS data, via the live Establishments API.

Only touches establishments where post_code IS NULL in establishments_current
(bulk data) and either haven't been checked before, or were checked more
than `postcode_recheck_after_days` ago (config.toml). Archives every raw
live-API page unmodified before parsing, same discipline as every other
data source. Writes the recovered postcode to a *separate* column
(postcode_from_live_api) -- never overwrites the bulk post_code column,
and never touches the fingerprint/observations machinery, so this can
never be misread as FHRS data changing (see fsa_pipeline/db.py module
docstring and README "Design notes").

Verified against real data (2026-08-25/26) that this only partially
resolves the gap -- some postcodes are missing at the source and the
live API doesn't have them either (e.g. West Lindsey, 0% recovered).

Idempotent per (authority, day): an authority already backfilled today is
skipped. Pass --force to rerun. Wired into run_daily.ps1, after FHRS
parsing and before matching.

Usage:
    python scripts/backfill_postcodes.py [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.archive import AlreadyArchived, day_dir, write_gzip_atomic
from fsa_pipeline.config import load_config
from fsa_pipeline.fhrs_live import fetch_page, meta_from_page_bytes, postcodes_from_page_bytes
from fsa_pipeline.http_client import build_session
from fsa_pipeline.logging_utils import setup_logger


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def page_filename(authority_code: str, index: int) -> str:
    return f"{authority_code}_page_{index:04d}.json.gz"


def get_or_fetch_page(session, config, logger, day_path: Path, authority_code: str, local_authority_id: int, page_number: int) -> bytes:
    dest = day_path / page_filename(authority_code, page_number)
    if dest.exists():
        with gzip.open(dest, "rb") as f:
            return f.read()

    content = fetch_page(session, config, local_authority_id, page_number)
    write_gzip_atomic(content, dest)
    return content


def fetch_authority_postcodes(session, config, logger, day_path: Path, authority_code: str, local_authority_id: int) -> dict:
    """Fetches every page for one authority and returns a merged FHRSID ->
    postcode map covering the whole authority."""
    postcodes: dict[int, str | None] = {}

    first_page = get_or_fetch_page(session, config, logger, day_path, authority_code, local_authority_id, 1)
    postcodes.update(postcodes_from_page_bytes(first_page))
    meta = meta_from_page_bytes(first_page)
    total_pages = meta.get("totalPages", 1) or 1

    for page_number in range(2, total_pages + 1):
        time.sleep(config.live_request_delay_seconds)
        try:
            page = get_or_fetch_page(session, config, logger, day_path, authority_code, local_authority_id, page_number)
        except AlreadyArchived:
            continue
        postcodes.update(postcodes_from_page_bytes(page))

    return postcodes


def run(force: bool) -> int:
    config = load_config()
    date_str = dt.date.today().isoformat()
    logger = setup_logger("backfill_postcodes", config.log_dir / f"backfill_postcodes_{date_str}.log")

    conn = db.connect(config.db_path)
    session = build_session(config)
    day_path = day_dir(config.live_raw_dir, date_str)

    candidates = db.get_postcode_backfill_candidates(conn, config.postcode_recheck_after_days)
    logger.info("%d candidate establishment(s) missing a postcode", len(candidates))

    by_authority: dict[str, list[int]] = defaultdict(list)
    for fhrsid, authority_code in candidates:
        by_authority[authority_code].append(fhrsid)

    logger.info("%d authorities to check", len(by_authority))

    total_checked = 0
    total_resolved = 0
    failed_authorities = []

    for i, (authority_code, fhrsids) in enumerate(sorted(by_authority.items()), start=1):
        if not force and db.already_backfilled(conn, authority_code, date_str):
            continue

        row = conn.execute("SELECT local_authority_id FROM authorities WHERE code = ?", (authority_code,)).fetchone()
        if row is None:
            logger.warning("%s: no authorities row, skipping", authority_code)
            continue
        local_authority_id = row[0]

        try:
            postcodes = fetch_authority_postcodes(session, config, logger, day_path, authority_code, local_authority_id)
        except Exception as e:
            failed_authorities.append(authority_code)
            logger.error("[%d/%d] %s: fetch FAILED: %s", i, len(by_authority), authority_code, e)
            db.record_postcode_backfill_run(
                conn, authority_code=authority_code, run_date=date_str,
                candidates_checked=0, resolved_count=0, status="error",
                error_message=str(e), run_at=now_iso(),
            )
            continue

        resolved = 0
        checked_at = now_iso()
        for fhrsid in fhrsids:
            postcode_value = postcodes.get(fhrsid)
            db.apply_postcode_backfill(conn, fhrsid, authority_code, postcode_value, checked_at)
            if postcode_value:
                resolved += 1

        total_checked += len(fhrsids)
        total_resolved += resolved

        db.record_postcode_backfill_run(
            conn, authority_code=authority_code, run_date=date_str,
            candidates_checked=len(fhrsids), resolved_count=resolved, status="ok", run_at=checked_at,
        )

        logger.info(
            "[%d/%d] %s: %d/%d resolved",
            i, len(by_authority), authority_code, resolved, len(fhrsids),
        )

    logger.info(
        "done: %d authorities, %d checked, %d resolved (%.1f%%), %d authorities failed",
        len(by_authority) - len(failed_authorities), total_checked, total_resolved,
        100 * total_resolved / total_checked if total_checked else 0.0,
        len(failed_authorities),
    )

    return 1 if failed_authorities else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rerun authorities already backfilled today")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(run(args.force))


if __name__ == "__main__":
    main()
